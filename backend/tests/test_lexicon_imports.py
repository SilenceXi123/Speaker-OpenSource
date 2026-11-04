from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_import import LexiconImport
from app.models.lexicon_occurrence import LexiconOccurrence
from app.models.lexicon_term import LexiconTerm
from app.services.lexicon_imports import (
    build_project_script_context,
    import_lexicon_candidates_from_bytes,
    import_lexicon_candidates_from_text,
    list_project_script_evidence,
    save_project_script_evidence,
)


def _session():
    local_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=local_engine)
    TestingSessionLocal = sessionmaker(bind=local_engine, autoflush=False, autocommit=False)
    return TestingSessionLocal()


def test_import_extracts_hard_organization_candidates_with_offsets() -> None:
    session = _session()
    text = "嘉宾来自北京元空智能科技有限公司，也提到了原力无限智能科技杭州有限公司。"
    try:
        result = import_lexicon_candidates_from_text(
            session,
            tenant_id="default",
            project_id="中国好团队",
            source_name="客户台本.txt",
            text=text,
        )
        session.commit()

        organizations = {
            candidate.display_text: candidate
            for candidate in result.candidates
            if candidate.term_type == "organization"
        }
        assert set(organizations) == {"北京元空智能科技有限公司", "原力无限智能科技杭州有限公司"}

        first = organizations["北京元空智能科技有限公司"]
        assert first.status == "suggested_active"
        assert first.suggested_protection_level == "hard"
        assert first.raw_start_char == text.index("北京元空智能科技有限公司")
        assert first.raw_end_char == first.raw_start_char + len("北京元空智能科技有限公司")
        assert first.normalized_start_char == first.raw_start_char
        assert first.normalized_end_char == first.raw_end_char

        assert result.import_record.source_name == "客户台本.txt"
        assert result.import_record.status == "parsed"
        assert result.import_record.candidate_count == len(result.candidates)
        assert result.import_record.rejected_count == 0
        assert session.scalar(select(LexiconImport).where(LexiconImport.id == result.import_record.id)) is not None
        assert session.scalar(select(LexiconTerm)) is None
    finally:
        session.close()


def test_project_script_evidence_is_saved_once_and_reusable() -> None:
    session = _session()
    try:
        first = save_project_script_evidence(
            session,
            tenant_id='default',
            project_id='project-a',
            source_name='第一份.txt',
            text='SHARK 的作品是冰棍儿',
        )
        duplicate = save_project_script_evidence(
            session,
            tenant_id='default',
            project_id='project-a',
            source_name='重复上传.txt',
            text='SHARK 的作品是冰棍儿',
        )
        save_project_script_evidence(
            session,
            tenant_id='default',
            project_id='project-a',
            source_name='第二份.txt',
            text='RENZER 的舞台很有 vibe',
        )
        session.commit()

        records = list_project_script_evidence(session, tenant_id='default', project_id='project-a')
        context = build_project_script_context(session, tenant_id='default', project_id='project-a')

        assert duplicate.id == first.id
        assert [record.source_name for record in records] == ['第一份.txt', '第二份.txt']
        assert context is not None
        assert context['summary']['source_count'] == 2
    finally:
        session.close()


def test_import_creates_occurrence_evidence_for_candidates() -> None:
    session = _session()
    text = "嘉宾来自北京元空智能科技有限公司。"
    try:
        result = import_lexicon_candidates_from_text(
            session,
            tenant_id="default",
            project_id="中国好团队",
            source_name="客户台本.txt",
            text=text,
            source_type="paste",
        )
        session.commit()

        candidate = next(item for item in result.candidates if item.display_text == "北京元空智能科技有限公司")
        occurrence = session.scalar(
            select(LexiconOccurrence).where(LexiconOccurrence.candidate_id == candidate.id)
        )

        assert occurrence is not None
        assert occurrence.import_id == result.import_record.id
        assert occurrence.term_id is None
        assert occurrence.project_id == "中国好团队"
        assert occurrence.source_type == "lexicon_import"
        assert occurrence.source_ref == "客户台本.txt"
        assert occurrence.raw_context and "北京元空智能科技有限公司" in occurrence.raw_context
        assert occurrence.raw_start_char == candidate.raw_start_char
        assert occurrence.raw_end_char == candidate.raw_end_char
    finally:
        session.close()


def test_forced_candidate_without_source_span_is_rejected_and_not_persisted() -> None:
    session = _session()
    try:
        result = import_lexicon_candidates_from_text(
            session,
            tenant_id="default",
            project_id="中国好团队",
            source_name="粘贴文本",
            text="这里只出现北京元空智能科技有限公司。",
            source_type="paste",
            forced_candidates=["不存在的品牌名"],
        )
        session.commit()

        assert result.rejected_count == 1
        assert result.import_record.rejected_count == 1
        assert all(candidate.display_text != "不存在的品牌名" for candidate in result.candidates)
        assert session.scalar(
            select(LexiconCandidate).where(LexiconCandidate.display_text == "不存在的品牌名")
        ) is None
    finally:
        session.close()


def test_import_from_bytes_reuses_script_text_extraction() -> None:
    session = _session()
    payload = "1\n00:00:01,000 --> 00:00:02,000\n欢迎来到《智能之夜》，地点是佛山古镇。\n".encode()
    try:
        result = import_lexicon_candidates_from_bytes(
            session,
            tenant_id="default",
            project_id="中国好团队",
            file_name="script.srt",
            payload=payload,
        )
        session.commit()

        by_text = {candidate.display_text: candidate for candidate in result.candidates}
        assert by_text["智能之夜"].term_type == "program"
        assert by_text["智能之夜"].status == "suggested_active"
        assert by_text["佛山古镇"].term_type == "place"
        assert result.import_record.source_name == "script.srt"
        assert result.import_record.source_type == "upload"
        assert result.import_record.candidate_count == len(result.candidates)
    finally:
        session.close()


def test_org_names_keep_embedded_context_characters() -> None:
    session = _session()
    text = "合作方包括上海和睦家医院有限公司，以及自在科技有限公司。"
    try:
        result = import_lexicon_candidates_from_text(
            session,
            tenant_id="default",
            project_id="中国好团队",
            source_name="客户名单.txt",
            text=text,
        )
        session.commit()

        organizations = {
            candidate.display_text: candidate
            for candidate in result.candidates
            if candidate.term_type == "organization"
        }
        assert "上海和睦家医院有限公司" in organizations
        assert "自在科技有限公司" in organizations
        assert "睦家医院有限公司" not in organizations
        assert "科技有限公司" not in organizations
        assert organizations["上海和睦家医院有限公司"].raw_start_char == text.index("上海和睦家医院有限公司")
        assert organizations["自在科技有限公司"].raw_start_char == text.index("自在科技有限公司")
    finally:
        session.close()


def test_duplicate_import_metrics_distinguish_returned_from_new_candidates() -> None:
    session = _session()
    text = "嘉宾来自北京元空智能科技有限公司。"
    try:
        first = import_lexicon_candidates_from_text(
            session,
            tenant_id="default",
            project_id="中国好团队",
            source_name="客户台本.txt",
            text=text,
        )
        session.commit()

        second = import_lexicon_candidates_from_text(
            session,
            tenant_id="default",
            project_id="中国好团队",
            source_name="客户台本.txt",
            text=text,
        )
        session.commit()

        assert first.import_record.candidate_count == 1
        assert first.import_record.metrics_json["returned_candidate_count"] == 1
        assert first.import_record.metrics_json["created_candidate_count"] == 1
        assert first.import_record.metrics_json["updated_candidate_count"] == 0
        assert second.import_record.candidate_count == 1
        assert second.import_record.metrics_json["returned_candidate_count"] == 1
        assert second.import_record.metrics_json["created_candidate_count"] == 0
        assert second.import_record.metrics_json["updated_candidate_count"] == 1
        assert len(list(session.scalars(select(LexiconCandidate)))) == 1
    finally:
        session.close()


def test_forced_candidate_overlapping_extracted_candidate_is_not_duplicated() -> None:
    session = _session()
    text = "嘉宾来自北京元空智能科技有限公司。"
    try:
        result = import_lexicon_candidates_from_text(
            session,
            tenant_id="default",
            project_id="中国好团队",
            source_name="粘贴文本",
            text=text,
            forced_candidates=[{"display_text": "北京元空智能科技有限公司", "term_type": "organization"}],
        )
        session.commit()

        matches = [candidate for candidate in result.candidates if candidate.display_text == "北京元空智能科技有限公司"]
        assert len(matches) == 1
        assert matches[0].extraction_method == "org_suffix_rule"
        assert matches[0].status == "suggested_active"
        assert result.import_record.candidate_count == 1
    finally:
        session.close()
