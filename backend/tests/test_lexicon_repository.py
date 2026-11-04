import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.lexicon_normalize as lexicon_normalize
from app.db.session import Base
from app.models.lexicon_occurrence import LexiconOccurrence
from app.models.lexicon_term import LexiconTerm
from app.services.lexicon_normalize import build_normalized_text_with_offset_map, normalize_term
from app.services.lexicon_repository import (
    create_project_term,
    disable_term_for_scope,
    list_project_candidates,
    promote_candidate_to_term,
    reject_candidate,
    upsert_candidate,
)


def _session():
    local_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=local_engine)
    TestingSessionLocal = sessionmaker(bind=local_engine, autoflush=False, autocommit=False)
    return TestingSessionLocal()


def test_normalize_term_simplifies_and_strips_display_punctuation() -> None:
    assert normalize_term("《人工 智能》") == "人工智能"
    assert normalize_term("原力無限智能科技杭州有限公司") == "原力无限智能科技杭州有限公司"


def test_offset_map_preserves_raw_span_for_normalized_text() -> None:
    result = build_normalized_text_with_offset_map("原力 無限")
    assert result.normalized_text == "原力 无限"
    assert result.raw_to_normalized[0] == 0
    assert result.normalized_to_raw[0] == 0


def test_offset_map_records_each_normalized_character_for_expansions(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_normalize(value: str) -> str:
        if value == "㍿":
            return "株式会社"
        return value

    monkeypatch.setattr(lexicon_normalize, "normalize_to_simplified_chinese", fake_normalize)

    result = build_normalized_text_with_offset_map("㍿A")
    assert result.normalized_text == "株式会社A"
    assert result.raw_to_normalized == [0, 4]
    assert result.normalized_to_raw == [0, 0, 0, 0, 1]


def test_create_project_term_normalizes_and_persists_active_term() -> None:
    session = _session()
    try:
        term = create_project_term(
            session,
            tenant_id="default",
            project_id="中国好团队",
            display_text="《原力 無限智能科技杭州有限公司》",
            term_type="organization",
            protection_level="hard",
            source_type="manual",
            created_by="admin",
        )
        session.commit()

        assert term.scope_type == "project"
        assert term.scope_id == "中国好团队"
        assert term.display_text == "《原力 無限智能科技杭州有限公司》"
        assert term.normalized_term == "原力无限智能科技杭州有限公司"
        assert term.status == "active"
        assert term.protection_level == "hard"
        assert term.review_status == "approved"
        assert term.source_type == "manual"
        assert term.created_by == "admin"

        retry = create_project_term(
            session,
            tenant_id="default",
            project_id="中国好团队",
            display_text="原力无限智能科技杭州有限公司",
            term_type="organization",
            protection_level="hard",
            source_type="manual",
            created_by="admin",
        )
        session.commit()
        assert retry.id == term.id

        saved = session.scalar(select(LexiconTerm).where(LexiconTerm.id == term.id))
        assert saved is not None
        assert saved.normalized_term == "原力无限智能科技杭州有限公司"
        assert saved.scope_id == "中国好团队"

        active_terms = list(
            session.scalars(
                select(LexiconTerm).where(
                    LexiconTerm.tenant_id == "default",
                    LexiconTerm.scope_type == "project",
                    LexiconTerm.scope_id == "中国好团队",
                    LexiconTerm.normalized_term == "原力无限智能科技杭州有限公司",
                    LexiconTerm.status == "active",
                )
            )
        )
        assert len(active_terms) == 1
    finally:
        session.close()


def test_upsert_candidate_rejects_missing_identity_fields() -> None:
    session = _session()
    kwargs = {
        "tenant_id": "default",
        "project_id": "中国好团队",
        "normalized_term": "北京元空智能科技有限公司",
        "display_text": "北京元空智能科技有限公司",
        "term_type": "organization",
        "status": "candidate",
        "suggested_protection_level": "soft",
        "extraction_method": "script_rule",
        "source_text_hash": "hash-a",
        "raw_start_char": 0,
        "raw_end_char": 12,
        "normalized_start_char": 0,
        "normalized_end_char": 12,
    }
    try:
        with pytest.raises(ValueError, match="extraction_method"):
            upsert_candidate(session, **{**kwargs, "extraction_method": ""})
        with pytest.raises(ValueError, match="source_text_hash"):
            upsert_candidate(session, **{**kwargs, "source_text_hash": " "})
    finally:
        session.close()


def test_upsert_candidate_rejects_invalid_span_offsets() -> None:
    session = _session()
    kwargs = {
        "tenant_id": "default",
        "project_id": "中国好团队",
        "normalized_term": "北京元空智能科技有限公司",
        "display_text": "北京元空智能科技有限公司",
        "term_type": "organization",
        "status": "candidate",
        "suggested_protection_level": "soft",
        "extraction_method": "script_rule",
        "source_text_hash": "hash-a",
        "raw_start_char": 0,
        "raw_end_char": 12,
        "normalized_start_char": 0,
        "normalized_end_char": 12,
    }
    try:
        with pytest.raises(ValueError, match="raw_start_char"):
            upsert_candidate(session, **{**kwargs, "raw_start_char": -1})
        with pytest.raises(ValueError, match="raw_start_char"):
            upsert_candidate(session, **{**kwargs, "raw_start_char": 13})
        with pytest.raises(ValueError, match="normalized_start_char"):
            upsert_candidate(session, **{**kwargs, "normalized_start_char": -1})
        with pytest.raises(ValueError, match="normalized_start_char"):
            upsert_candidate(session, **{**kwargs, "normalized_start_char": 13})
    finally:
        session.close()


def test_candidate_lifecycle_promote_reject_and_disable() -> None:
    session = _session()
    try:
        candidate = upsert_candidate(
            session,
            tenant_id="default",
            project_id="中国好团队",
            normalized_term="北京元空智能科技有限公司",
            display_text="北京元空智能科技有限公司",
            term_type="organization",
            status="suggested_active",
            suggested_protection_level="hard",
            extraction_method="script_rule",
            source_text_hash="hash-a",
            raw_start_char=0,
            raw_end_char=12,
            normalized_start_char=0,
            normalized_end_char=12,
            confidence=0.93,
            context="北京元空智能科技有限公司创始人",
        )
        session.commit()

        candidates = list_project_candidates(session, tenant_id="default", project_id="中国好团队")
        assert [item.normalized_term for item in candidates] == ["北京元空智能科技有限公司"]

        term = promote_candidate_to_term(session, candidate.id, created_by="admin")
        session.commit()
        assert term.scope_type == "project"
        assert term.scope_id == "中国好团队"
        assert term.status == "active"
        assert term.protection_level == "hard"
        assert candidate.status == "promoted"
        assert candidate.promoted_term_id == term.id

        override = disable_term_for_scope(
            session,
            term_id=term.id,
            normalized_term=term.normalized_term,
            scope_type="project",
            scope_id="中国好团队",
            reason="本项目测试禁用",
            created_by="admin",
        )
        session.commit()
        assert override.action == "disable"

        other = upsert_candidate(
            session,
            tenant_id="default",
            project_id="中国好团队",
            normalized_term="普通候选词",
            display_text="普通候选词",
            term_type="term",
            status="candidate",
            suggested_protection_level="soft",
            extraction_method="jieba",
            source_text_hash="hash-b",
            raw_start_char=0,
            raw_end_char=5,
            normalized_start_char=0,
            normalized_end_char=5,
            confidence=0.55,
            context="普通候选词",
        )
        reject_candidate(session, other.id, reviewed_by="admin", reason="不是专名")
        session.commit()
        assert other.status == "rejected"
    finally:
        session.close()


def test_promote_candidate_associates_occurrence_evidence_and_updates_count() -> None:
    session = _session()
    try:
        candidate = upsert_candidate(
            session,
            tenant_id="default",
            project_id="中国好团队",
            normalized_term="北京元空智能科技有限公司",
            display_text="北京元空智能科技有限公司",
            term_type="organization",
            status="suggested_active",
            suggested_protection_level="hard",
            extraction_method="script_rule",
            source_text_hash="hash-a",
            raw_start_char=4,
            raw_end_char=16,
            normalized_start_char=4,
            normalized_end_char=16,
            confidence=0.93,
            context="嘉宾来自北京元空智能科技有限公司。",
        )
        occurrence = LexiconOccurrence(
            tenant_id="default",
            candidate_id=candidate.id,
            import_id="import-a",
            project_id="中国好团队",
            source_type="lexicon_import",
            source_ref="客户台本.txt",
            raw_context=candidate.context,
            normalized_context=candidate.context,
            raw_start_char=candidate.raw_start_char,
            raw_end_char=candidate.raw_end_char,
            normalized_start_char=candidate.normalized_start_char,
            normalized_end_char=candidate.normalized_end_char,
        )
        session.add(occurrence)
        session.commit()

        term = promote_candidate_to_term(session, candidate.id, created_by="admin")
        session.commit()
        session.refresh(occurrence)
        session.refresh(term)

        assert occurrence.term_id == term.id
        assert term.occurrence_count == 1
    finally:
        session.close()
