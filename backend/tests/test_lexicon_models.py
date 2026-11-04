from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_import import LexiconImport
from app.models.lexicon_occurrence import LexiconOccurrence
from app.models.lexicon_term import LexiconTerm
from app.models.lexicon_term_alias import LexiconTermAlias
from app.models.lexicon_term_override import LexiconTermOverride


def test_lexicon_tables_create_and_query() -> None:
    local_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=local_engine)
    TestingSessionLocal = sessionmaker(bind=local_engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        term = LexiconTerm(
            tenant_id="default",
            scope_type="project",
            scope_id="中国好团队",
            normalized_term="北京元空智能科技有限公司",
            display_text="北京元空智能科技有限公司",
            term_type="organization",
            status="active",
            protection_level="hard",
            review_status="approved",
            source_type="admin_import",
            source_ref="manual",
        )
        session.add(term)
        session.flush()
        term_id = term.id

        candidate = LexiconCandidate(
            tenant_id="default",
            project_id="中国好团队",
            normalized_term="原力无限智能科技杭州有限公司",
            display_text="原力无限智能科技杭州有限公司",
            term_type="organization",
            status="suggested_active",
            suggested_protection_level="hard",
            extraction_method="script_rule",
            source_text_hash="hash-a",
            raw_start_char=10,
            raw_end_char=24,
            normalized_start_char=10,
            normalized_end_char=24,
            confidence=0.92,
        )
        session.add(candidate)
        session.flush()
        candidate_id = candidate.id

        lexicon_import = LexiconImport(
            tenant_id="default",
            project_id="中国好团队",
            source_name="台本.txt",
            source_type="upload",
            source_text_hash="hash-a",
            parser_version="lexicon_imports.v1",
            status="parsed",
            candidate_count=1,
            promoted_count=0,
            rejected_count=0,
            error_message=None,
            metrics_json={"raw_text_chars": 42},
        )
        session.add(lexicon_import)
        session.flush()
        import_id = lexicon_import.id

        occurrence = LexiconOccurrence(
            tenant_id="default",
            term_id=term_id,
            candidate_id=candidate_id,
            import_id=import_id,
            job_id="job-1",
            project_id="中国好团队",
            source_type="script_import",
            source_ref="台本.txt",
            raw_context="来自北京元空智能科技有限公司",
            normalized_context="来自北京元空智能科技有限公司",
            raw_start_char=2,
            raw_end_char=16,
            normalized_start_char=2,
            normalized_end_char=16,
        )
        session.add(occurrence)

        override = LexiconTermOverride(
            tenant_id="default",
            term_id=term_id,
            normalized_term=term.normalized_term,
            scope_type="project",
            scope_id="中国好团队",
            action="disable",
            reason="本项目不需要保护",
            created_by="admin",
        )
        session.add(override)

        alias = LexiconTermAlias(
            tenant_id="default",
            term_id=term_id,
            alias_text="元空智能",
            normalized_alias="元空智能",
            status="active",
        )
        session.add(alias)
        session.flush()
        occurrence_id = occurrence.id
        override_id = override.id
        alias_id = alias.id
        session.commit()

        saved = session.scalar(select(LexiconTerm).where(LexiconTerm.id == term_id))
        assert saved is not None
        assert saved.protection_level == "hard"
        assert saved.status == "active"
        assert saved.review_status == "approved"

        saved_candidate = session.scalar(select(LexiconCandidate).where(LexiconCandidate.id == candidate_id))
        assert saved_candidate is not None
        assert saved_candidate.status == "suggested_active"

        saved_import = session.scalar(select(LexiconImport).where(LexiconImport.id == import_id))
        assert saved_import is not None
        assert saved_import.source_text_hash == "hash-a"

        saved_occurrence = session.scalar(select(LexiconOccurrence).where(LexiconOccurrence.id == occurrence_id))
        assert saved_occurrence is not None
        assert saved_occurrence.job_id == "job-1"

        saved_override = session.scalar(select(LexiconTermOverride).where(LexiconTermOverride.id == override_id))
        assert saved_override is not None
        assert saved_override.action == "disable"

        saved_alias = session.scalar(select(LexiconTermAlias).where(LexiconTermAlias.id == alias_id))
        assert saved_alias is not None
        assert saved_alias.normalized_alias == "元空智能"
    finally:
        session.close()
