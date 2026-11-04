from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.job import Job
from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_term import LexiconTerm
from app.models.project import Project
from app.services.normalize import normalize_funasr_payload
from app.services.pipeline_artifacts import build_pipeline_artifacts
from app.services.utterance_rebuilder import rebuild_semantic_utterances
from main import app


@pytest.fixture()
def lexicon_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    monkeypatch.setattr("app.api.v1.lexicon.SessionLocal", session_factory)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(lexicon_session: Session) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _import_candidates(client: TestClient, project_id: str = "project-a") -> list[dict]:
    response = client.post(
        "/api/v1/lexicon/imports",
        data={
            "project_id": project_id,
            "source_name": "客户台本.txt",
            "text": "嘉宾来自北京元空智能科技有限公司，也提到了佛山古镇。",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["project_id"] == project_id
    assert payload["candidate_count"] >= 1
    assert payload["candidates"]
    return payload["candidates"]


def test_text_import_creates_candidates(client: TestClient) -> None:
    candidates = _import_candidates(client)

    assert "北京元空智能科技有限公司" in {candidate["display_text"] for candidate in candidates}


def test_get_candidates_returns_imported_items(client: TestClient) -> None:
    _import_candidates(client)

    response = client.get("/api/v1/lexicon/candidates", params={"project_id": "project-a"})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) >= {"items"}
    assert "北京元空智能科技有限公司" in {candidate["display_text"] for candidate in payload["items"]}


def test_uuid_project_management_includes_legacy_alias_terms_and_candidates(
    client: TestClient,
    lexicon_session: Session,
) -> None:
    project = Project(
        id="project-uuid",
        name="旧词库项目",
        name_key="旧词库项目",
        legacy_program_key="旧词库项目",
    )
    term = LexiconTerm(
        tenant_id="default",
        scope_type="project",
        scope_id="旧词库项目",
        display_text="蓝鲸智造",
        normalized_term="蓝鲸智造",
        term_type="term",
        source_type="manual",
        protection_level="strong",
        status="active",
        review_status="approved",
    )
    candidate = LexiconCandidate(
        tenant_id="default",
        project_id="旧词库项目",
        display_text="元力无限",
        normalized_term="元力无限",
        term_type="organization",
        status="candidate",
        suggested_protection_level="strong",
        extraction_method="script",
        source_text_hash="hash-1",
    )
    lexicon_session.add_all([project, term, candidate])
    lexicon_session.commit()

    terms_response = client.get("/api/v1/lexicon/terms", params={"project_id": project.id})
    candidates_response = client.get("/api/v1/lexicon/candidates", params={"project_id": project.id})
    preview_response = client.post(
        "/api/v1/lexicon/runtime-bundle/preview",
        json={"project_id": project.id},
    )

    assert terms_response.status_code == 200
    assert term.id in {item["id"] for item in terms_response.json()["items"]}
    assert candidates_response.status_code == 200
    assert candidate.id in {item["id"] for item in candidates_response.json()["items"]}
    assert preview_response.status_code == 200
    assert "蓝鲸智造" in {item["display_text"] for item in preview_response.json()["terms"]}


def test_create_term_rejects_overlong_sentence_like_text(client: TestClient) -> None:
    response = client.post(
        "/api/v1/lexicon/terms",
        json={
            "project_id": "project-a",
            "display_text": "社区学院看一看看他们是如何打通邻里之间",
            "protection_level": "strong",
        },
    )

    assert response.status_code == 400
    assert "整句" in response.json()["detail"]


def test_promote_candidate_returns_active_term_and_preview_includes_term(client: TestClient) -> None:
    candidate = _import_candidates(client)[0]

    promote_response = client.post(f"/api/v1/lexicon/candidates/{candidate['id']}/promote")

    assert promote_response.status_code == 200
    term = promote_response.json()
    assert term["status"] == "active"
    assert term["review_status"] == "approved"
    assert term["display_text"] == candidate["display_text"]
    assert term["occurrence_count"] >= 1

    preview_response = client.post(
        "/api/v1/lexicon/runtime-bundle/preview",
        json={"project_id": "project-a"},
    )

    assert preview_response.status_code == 200
    bundle = preview_response.json()
    assert bundle["term_set_hash"]
    assert term["display_text"] in {item["display_text"] for item in bundle["terms"]}

    terms_response = client.get("/api/v1/lexicon/terms", params={"project_id": "project-a"})

    assert terms_response.status_code == 200
    terms_payload = terms_response.json()
    assert set(terms_payload) >= {"items"}
    assert term["id"] in {item["id"] for item in terms_payload["items"]}

    occurrences_response = client.get(f"/api/v1/lexicon/terms/{term['id']}/occurrences")

    assert occurrences_response.status_code == 200
    occurrences_payload = occurrences_response.json()
    assert set(occurrences_payload) >= {"items"}
    assert len(occurrences_payload["items"]) >= 1
    assert occurrences_payload["items"][0]["candidate_id"] == candidate["id"]
    assert occurrences_payload["items"][0]["import_id"]
    assert occurrences_payload["items"][0]["raw_context"]


def test_promote_missing_candidate_returns_404(client: TestClient) -> None:
    response = client.post("/api/v1/lexicon/candidates/missing-candidate/promote")

    assert response.status_code == 404


def test_promote_already_promoted_candidate_returns_409(client: TestClient) -> None:
    candidate = _import_candidates(client)[0]

    first_response = client.post(f"/api/v1/lexicon/candidates/{candidate['id']}/promote")
    second_response = client.post(f"/api/v1/lexicon/candidates/{candidate['id']}/promote")

    assert first_response.status_code == 200
    assert second_response.status_code == 409


def test_reject_candidate_returns_rejected_candidate(client: TestClient, lexicon_session: Session) -> None:
    _import_candidates(client)
    candidate = (
        lexicon_session.query(LexiconCandidate)
        .filter(LexiconCandidate.status.in_(("candidate", "suggested_active")))
        .order_by(LexiconCandidate.created_at.asc())
        .first()
    )
    assert candidate is not None

    response = client.post(
        f"/api/v1/lexicon/candidates/{candidate.id}/reject",
        json={"reason": "不纳入本项目"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == candidate.id
    assert payload["status"] == "rejected"


def test_reject_missing_candidate_returns_404(client: TestClient) -> None:
    response = client.post("/api/v1/lexicon/candidates/missing-candidate/reject")

    assert response.status_code == 404


def test_reject_already_rejected_candidate_returns_409(client: TestClient, lexicon_session: Session) -> None:
    _import_candidates(client)
    candidate = (
        lexicon_session.query(LexiconCandidate)
        .filter(LexiconCandidate.status.in_(("candidate", "suggested_active")))
        .order_by(LexiconCandidate.created_at.asc())
        .first()
    )
    assert candidate is not None

    first_response = client.post(f"/api/v1/lexicon/candidates/{candidate.id}/reject")
    second_response = client.post(f"/api/v1/lexicon/candidates/{candidate.id}/reject")

    assert first_response.status_code == 200
    assert second_response.status_code == 409


def test_empty_import_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/v1/lexicon/imports",
        data={"project_id": "project-a", "text": "   "},
    )

    assert response.status_code == 400


def test_transcript_job_runtime_bundle_rebuild_returns_409(
    client: TestClient,
    lexicon_session: Session,
) -> None:
    job = Job(
        kind="transcript",
        source_url="https://example.com/source.wav",
        status="completed",
        program_key="project-a",
        pipeline_artifacts={"existing": True},
    )
    lexicon_session.add(job)
    lexicon_session.commit()

    response = client.post(f"/api/v1/lexicon/jobs/{job.id}/runtime-bundle/rebuild")

    assert response.status_code == 409
    assert job.pipeline_artifacts == {"existing": True}


def test_runtime_bundle_rebuild_rejects_another_project_id(
    client: TestClient,
    lexicon_session: Session,
) -> None:
    job = Job(
        kind="transcription",
        source_url="https://example.com/source.wav",
        status="completed",
        project_id="project-a",
        program_key="项目A",
        program_name="项目A",
        pipeline_artifacts={"existing": True},
    )
    lexicon_session.add(job)
    lexicon_session.commit()

    response = client.post(
        f"/api/v1/lexicon/jobs/{job.id}/runtime-bundle/rebuild",
        json={"project_id": "project-b"},
    )

    assert response.status_code == 409
    assert "不一致" in response.json()["detail"]
    lexicon_session.refresh(job)
    assert job.pipeline_artifacts == {"existing": True}


def test_none_kind_job_runtime_bundle_rebuild_returns_409(
    client: TestClient,
    lexicon_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = Job(
        kind="transcription",
        source_url="https://example.com/source.wav",
        status="completed",
        program_key="project-a",
        pipeline_artifacts={"existing": True},
    )
    lexicon_session.add(job)
    lexicon_session.commit()
    job.kind = None
    monkeypatch.setattr("app.api.v1.lexicon.SessionLocal", lambda: lexicon_session)

    response = client.post(f"/api/v1/lexicon/jobs/{job.id}/runtime-bundle/rebuild")

    assert response.status_code == 409
    assert job.pipeline_artifacts == {"existing": True}


def test_transcription_runtime_bundle_rebuild_refreshes_full_pipeline_artifacts(
    client: TestClient,
    lexicon_session: Session,
) -> None:
    raw_payload = {
        "transcripts": [
            {
                "sentences": [
                    {
                        "sentence_id": 1,
                        "speaker_id": 0,
                        "begin_time": 0,
                        "end_time": 2000,
                        "text": "我们支持蓝鲸智造方案",
                        "words": [
                            {"text": "我们", "begin_time": 0, "end_time": 300},
                            {"text": "支持", "begin_time": 300, "end_time": 600},
                            {"text": "蓝鲸", "begin_time": 600, "end_time": 900},
                            {"text": "智造", "begin_time": 900, "end_time": 1200},
                            {"text": "方案", "begin_time": 1200, "end_time": 2000},
                        ],
                    }
                ]
            }
        ]
    }
    normalized_segments = normalize_funasr_payload(raw_payload)
    job = Job(
        kind="transcription",
        source_url="https://example.com/source.wav",
        status="completed",
        program_key="project-a",
        raw_payload=raw_payload,
        normalized_segments=normalized_segments,
        export_frame_rate="25",
        subtitle_strategy="enhanced",
    )
    lexicon_session.add(job)
    lexicon_session.commit()
    lexicon_session.refresh(job)
    raw_segments = normalize_funasr_payload(raw_payload)
    job.pipeline_artifacts = build_pipeline_artifacts(
        job,
        raw_segments=raw_segments,
        semantic_utterances=rebuild_semantic_utterances(raw_segments),
        normalized_segments=normalized_segments,
        refreshed_by="test_initial",
    )
    lexicon_session.commit()
    initial_runtime_hash = job.pipeline_artifacts["artifact_hashes"]["runtime_lexicon_bundle_hash"]

    response = client.post(
        f"/api/v1/lexicon/jobs/{job.id}/runtime-bundle/rebuild",
        json={
            "temporary_terms": [
                {
                    "display_text": "蓝鲸智造",
                    "term_type": "project_term",
                    "protection_level": "strong",
                }
            ]
        },
    )

    assert response.status_code == 200
    bundle = response.json()
    lexicon_session.refresh(job)
    artifacts = job.pipeline_artifacts
    assert bundle["term_set_hash"] != initial_runtime_hash
    assert artifacts["runtime_lexicon_bundle_snapshot"]["term_set_hash"] == bundle["term_set_hash"]
    assert artifacts["artifact_hashes"]["runtime_lexicon_bundle_hash"] == bundle["term_set_hash"]
    assert artifacts["artifact_hashes"]["subtitle_cues_hash"] == artifacts["export_manifest"]["cue_hash"]
    assert artifacts["subtitle_cues_snapshot"]["count"] >= 1
    assert artifacts["cue_validation_report"]["valid"] is True
    assert artifacts["artifact_hashes"]["cue_validation_hash"]
    assert any(
        item["text"] == "蓝鲸智造" and item["kind"] == "runtime_lexicon_strong"
        for item in artifacts["lexical_atoms_snapshot"]["items"]
    )
    assert artifacts["refreshed_by"] == "runtime_lexicon_bundle_rebuild"
