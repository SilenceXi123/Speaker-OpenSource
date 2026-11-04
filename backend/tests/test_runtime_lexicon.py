from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.lexicon_term_alias import LexiconTermAlias
from app.services.lexicon_repository import create_project_term, disable_term_for_scope
from app.services.runtime_lexicon import BUNDLE_VERSION, build_runtime_lexicon_bundle


def _session():
    local_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=local_engine)
    testing_session_local = sessionmaker(bind=local_engine, autoflush=False, autocommit=False)
    return testing_session_local()


def _terms_by_normalized(bundle: dict) -> dict[str, dict]:
    return {term["normalized_term"]: term for term in bundle["terms"]}


def test_runtime_bundle_prioritizes_project_over_global_and_disable_overrides() -> None:
    session = _session()
    try:
        global_term = create_project_term(
            session,
            tenant_id="default",
            project_id=None,
            scope_type="global",
            display_text="人工智能",
            term_type="concept",
            protection_level="hard",
            source_type="manual",
            created_by="admin",
        )
        create_project_term(
            session,
            tenant_id="default",
            project_id=None,
            scope_type="global",
            display_text="北京元空智能科技有限公司",
            term_type="organization",
            protection_level="soft",
            source_type="manual",
            created_by="admin",
        )
        project_term = create_project_term(
            session,
            tenant_id="default",
            project_id="project-a",
            scope_type="project",
            display_text="北京元空智能科技有限公司",
            term_type="organization",
            protection_level="hard",
            source_type="manual",
            created_by="admin",
        )
        disable_term_for_scope(
            session,
            term_id=global_term.id,
            normalized_term=global_term.normalized_term,
            scope_type="project",
            scope_id="project-a",
            reason="项目禁用",
            created_by="admin",
        )
        session.commit()

        bundle = build_runtime_lexicon_bundle(
            session,
            tenant_id="default",
            project_id="project-a",
            job_id="job-a",
            temporary_terms=[
                {
                    "display_text": "原力无限",
                    "term_type": "organization",
                    "protection_level": "hard",
                }
            ],
        )

        terms = _terms_by_normalized(bundle)
        assert bundle["bundle_version"] == BUNDLE_VERSION
        assert bundle["term_set_hash"]
        assert terms["北京元空智能科技有限公司"]["term_id"] == project_term.id
        assert terms["北京元空智能科技有限公司"]["scope_type"] == "project"
        assert terms["原力无限"]["source_type"] == "temporary"
        assert terms["原力无限"]["scope_type"] == "temporary"
        assert "人工智能" not in terms
        assert [item["normalized_term"] for item in bundle["disabled_terms"]] == ["人工智能"]
        assert bundle["source_counts"]["project"] == 1
        assert bundle["source_counts"]["temporary"] == 1
    finally:
        session.close()


def test_runtime_bundle_hash_is_stable_for_equivalent_terms() -> None:
    session = _session()
    try:
        create_project_term(
            session,
            tenant_id="default",
            project_id="project-a",
            scope_type="project",
            display_text="北京元空智能科技有限公司",
            term_type="organization",
            protection_level="hard",
            source_type="manual",
            created_by="admin",
        )
        session.commit()

        first = build_runtime_lexicon_bundle(
            session,
            tenant_id="default",
            project_id="project-a",
            temporary_terms=[{"display_text": "原力无限", "protection_level": "hard"}],
        )
        second = build_runtime_lexicon_bundle(
            session,
            tenant_id="default",
            project_id="project-a",
            temporary_terms=[{"display_text": "原力无限", "protection_level": "hard"}],
        )

        assert first["generated_at"] != second["generated_at"]
        assert first["term_set_hash"] == second["term_set_hash"]
    finally:
        session.close()


def test_runtime_bundle_disable_by_term_id_preserves_distinct_project_term() -> None:
    session = _session()
    try:
        global_term = create_project_term(
            session,
            tenant_id="default",
            project_id=None,
            scope_type="global",
            display_text="人工智能",
            term_type="concept",
            protection_level="hard",
            source_type="manual",
            created_by="admin",
        )
        project_term = create_project_term(
            session,
            tenant_id="default",
            project_id="project-a",
            scope_type="project",
            display_text="人工智能",
            term_type="concept",
            protection_level="hard",
            source_type="manual",
            created_by="admin",
        )
        disable_term_for_scope(
            session,
            term_id=global_term.id,
            normalized_term=global_term.normalized_term,
            scope_type="project",
            scope_id="project-a",
            reason="只禁用全局来源",
            created_by="admin",
        )
        session.commit()

        bundle = build_runtime_lexicon_bundle(session, tenant_id="default", project_id="project-a")

        terms = _terms_by_normalized(bundle)
        assert terms["人工智能"]["term_id"] == project_term.id
        assert terms["人工智能"]["scope_type"] == "project"
        assert [item["term_id"] for item in bundle["disabled_terms"]] == [global_term.id]
    finally:
        session.close()


def test_runtime_bundle_hash_and_terms_are_stable_for_reordered_temporary_terms() -> None:
    session = _session()
    try:
        first = build_runtime_lexicon_bundle(
            session,
            tenant_id="default",
            project_id="project-a",
            temporary_terms=[
                {"display_text": "原力无限", "term_type": "organization", "protection_level": "hard"},
                {"display_text": "北京元空", "term_type": "organization", "protection_level": "strong"},
                {"display_text": "原力无限", "term_type": "organization", "protection_level": "soft"},
            ],
        )
        second = build_runtime_lexicon_bundle(
            session,
            tenant_id="default",
            project_id="project-a",
            temporary_terms=[
                {"display_text": "原力无限", "term_type": "organization", "protection_level": "soft"},
                {"display_text": "北京元空", "term_type": "organization", "protection_level": "strong"},
                {"display_text": "原力无限", "term_type": "organization", "protection_level": "hard"},
            ],
        )

        assert first["term_set_hash"] == second["term_set_hash"]
        assert first["terms"] == second["terms"]
    finally:
        session.close()


def test_runtime_bundle_loads_uuid_project_and_legacy_alias_terms() -> None:
    session = _session()
    try:
        legacy_term = create_project_term(
            session,
            tenant_id='default',
            project_id='旧项目名称',
            scope_type='project',
            display_text='国际赛艇中心',
            term_type='organization',
            protection_level='hard',
            source_type='manual',
            created_by='admin',
        )
        uuid_term = create_project_term(
            session,
            tenant_id='default',
            project_id='project-uuid',
            scope_type='project',
            display_text='元力无限',
            term_type='organization',
            protection_level='hard',
            source_type='manual',
            created_by='admin',
        )
        session.commit()

        bundle = build_runtime_lexicon_bundle(
            session,
            tenant_id='default',
            project_id='project-uuid',
            project_aliases=['旧项目名称'],
            job_id='job-a',
        )

        terms = _terms_by_normalized(bundle)
        assert bundle['project_id'] == 'project-uuid'
        assert bundle['project_aliases'] == ['旧项目名称']
        assert terms['国际赛艇中心']['term_id'] == legacy_term.id
        assert terms['元力无限']['term_id'] == uuid_term.id
    finally:
        session.close()


def test_runtime_bundle_includes_active_database_aliases() -> None:
    session = _session()
    try:
        term = create_project_term(
            session,
            tenant_id='default',
            project_id='project-a',
            scope_type='project',
            display_text='RANZER叶润泽',
            term_type='person',
            protection_level='hard',
            source_type='manual',
            created_by='client',
        )
        session.add(LexiconTermAlias(
            tenant_id='default',
            term_id=term.id,
            alias_text='Ranzer',
            normalized_alias='ranzer',
            status='active',
        ))
        session.commit()

        bundle = build_runtime_lexicon_bundle(
            session,
            tenant_id='default',
            project_id='project-a',
        )

        assert _terms_by_normalized(bundle)['RANZER叶润泽']['aliases'] == ['Ranzer']
    finally:
        session.close()


def test_runtime_bundle_preserves_gold_temporary_aliases() -> None:
    session = _session()
    try:
        bundle = build_runtime_lexicon_bundle(
            session,
            tenant_id='default',
            project_id='project-a',
            temporary_terms=[{
                'display_text': 'SHarK米尔艾力',
                'term_type': 'person',
                'protection_level': 'hard',
                'aliases': ['SHarK', '米尔艾力'],
            }],
        )

        term = _terms_by_normalized(bundle)['SHarK米尔艾力']
        assert term['display_text'] == 'SHarK米尔艾力'
        assert term['aliases'] == ['SHarK', '米尔艾力']
    finally:
        session.close()


def test_runtime_bundle_only_loads_the_selected_knowledge_domain() -> None:
    session = _session()
    try:
        create_project_term(
            session,
            tenant_id='default',
            project_id='broadcast',
            scope_type='domain',
            display_text='广播领域词',
            source_type='manual',
        )
        create_project_term(
            session,
            tenant_id='default',
            project_id='classical_poetry',
            scope_type='domain',
            display_text='古诗词领域词',
            source_type='manual',
        )
        session.commit()

        broadcast = build_runtime_lexicon_bundle(
            session,
            tenant_id='default',
            project_id='project-a',
            domain_id='broadcast',
        )
        poetry = build_runtime_lexicon_bundle(
            session,
            tenant_id='default',
            project_id='project-b',
            domain_id='classical_poetry',
        )

        assert '广播领域词' in _terms_by_normalized(broadcast)
        assert '古诗词领域词' not in _terms_by_normalized(broadcast)
        assert '古诗词领域词' in _terms_by_normalized(poetry)
        assert '广播领域词' not in _terms_by_normalized(poetry)
    finally:
        session.close()
