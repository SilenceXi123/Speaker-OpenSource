from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.knowledge_correction_evidence import KnowledgeCorrectionEvidence
from app.models.lexicon_term import LexiconTerm
from app.models.lexicon_term_alias import LexiconTermAlias
from app.models.project import Project
from app.services.knowledge_learning import learn_from_user_edit_patch
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.runtime_lexicon import build_runtime_lexicon_bundle
from app.services.script_correction_candidates import (
    build_correction_candidates,
    build_script_snapshot,
)
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.user_edit_patches import build_segment_edit_patch


def _session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _project(session, project_id: str, *, domain: str = 'broadcast') -> Project:
    project = Project(
        id=project_id,
        tenant_id='default',
        name=project_id,
        name_key=project_id,
        knowledge_domain=domain,
    )
    session.add(project)
    session.flush()
    return project


def _learn(
    session,
    *,
    project_id: str,
    job_id: str,
    before: str = '扎记村',
    after: str = '查济村',
):
    job = Job(
        id=job_id,
        project_id=project_id,
        source_url=f'https://example.com/{job_id}.wav',
    )
    session.add(job)
    session.flush()
    patch = build_segment_edit_patch(
        [{'segmentId': 'segment-1', 'text': before}],
        [{'segmentId': 'segment-1', 'text': after}],
        source='manual_review',
    )
    return learn_from_user_edit_patch(session, job=job, edit_patch=patch)


def _candidate_from_bundle(bundle: dict):
    raw = build_raw_asr_snapshot(
        {'transcripts': [{'sentences': [{
            'speaker_id': 0,
            'begin_time': 0,
            'end_time': 300,
            'text': '欢迎来到扎记村',
            'words': [
                {'text': '欢迎', 'begin_time': 0, 'end_time': 100},
                {'text': '来到', 'begin_time': 100, 'end_time': 200},
                {'text': '扎记村', 'begin_time': 200, 'end_time': 300},
            ],
        }]}]},
        provider_model='fun-asr',
        provider_task_id='knowledge-test',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': 300,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )
    return build_correction_candidates(
        run_id='run-knowledge-test',
        raw_snapshot=raw,
        semantic_artifact=build_semantic_utterances_v2(raw),
        script_snapshot=build_script_snapshot(''),
        runtime_lexicon_bundle=bundle,
    )


def test_two_independent_job_edits_promote_project_knowledge_and_feed_pipeline() -> None:
    session = _session()
    _project(session, 'project-a')

    first = _learn(session, project_id='project-a', job_id='job-1')
    second = _learn(session, project_id='project-a', job_id='job-2')

    assert first.evidence_added == 1
    assert first.promoted_scopes == ()
    assert second.promoted_scopes == ('project',)
    term = session.scalar(select(LexiconTerm).where(
        LexiconTerm.scope_type == 'project',
        LexiconTerm.scope_id == 'project-a',
        LexiconTerm.normalized_term == '查济村',
    ))
    assert term is not None
    assert term.status == 'active'
    assert term.source_type == 'auto_user_edit'
    alias = session.scalar(select(LexiconTermAlias).where(
        LexiconTermAlias.term_id == term.id,
        LexiconTermAlias.normalized_alias == '扎记村',
    ))
    assert alias is not None and alias.status == 'active'

    bundle = build_runtime_lexicon_bundle(
        session,
        tenant_id='default',
        project_id='project-a',
        domain_id='broadcast',
    )
    assert bundle['learned_corrections'][0]['source_text'] == '扎记村'
    assert bundle['learned_corrections'][0]['replacement_text'] == '查济村'
    candidates = _candidate_from_bundle(bundle)
    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('扎记村', '查济村'),
    ]
    assert candidates[0].candidate_type == 'lexicon'
    assert 'knowledge-scope:project' in candidates[0].evidence_refs


def test_repeated_cross_project_evidence_promotes_domain_then_global() -> None:
    session = _session()
    results = []
    for project_index in range(5):
        project_id = f'project-{project_index}'
        _project(session, project_id, domain='broadcast')
        for job_index in range(2):
            results.append(_learn(
                session,
                project_id=project_id,
                job_id=f'job-{project_index}-{job_index}',
            ))

    assert any('domain' in result.promoted_scopes for result in results)
    assert 'global' in results[-1].promoted_scopes
    scopes = set(session.scalars(select(LexiconTerm.scope_type).where(
        LexiconTerm.normalized_term == '查济村',
        LexiconTerm.status == 'active',
    )))
    assert {'project', 'domain', 'global'}.issubset(scopes)


def test_conflicting_project_edit_disables_previously_learned_alias() -> None:
    session = _session()
    _project(session, 'project-a')
    _learn(session, project_id='project-a', job_id='job-1')
    _learn(session, project_id='project-a', job_id='job-2')

    conflict = _learn(
        session,
        project_id='project-a',
        job_id='job-3',
        before='扎记村',
        after='扎济村',
    )

    assert conflict.disabled_scopes == ('project',)
    alias = session.scalar(select(LexiconTermAlias).where(
        LexiconTermAlias.normalized_alias == '扎记村',
    ))
    assert alias is not None and alias.status == 'disabled'
    term = session.get(LexiconTerm, alias.term_id)
    assert term is not None and term.status == 'disabled'


def test_particle_feedback_is_recorded_but_never_promoted_as_a_term() -> None:
    session = _session()
    _project(session, 'project-a')
    _learn(
        session,
        project_id='project-a',
        job_id='job-1',
        before='的',
        after='得',
    )
    second = _learn(
        session,
        project_id='project-a',
        job_id='job-2',
        before='的',
        after='得',
    )

    assert second.promoted_scopes == ()
    evidence = list(session.scalars(select(KnowledgeCorrectionEvidence)))
    assert len(evidence) == 2
    assert all(not item.promotable for item in evidence)
    assert session.scalar(select(LexiconTerm).where(
        LexiconTerm.normalized_term == '得',
    )) is None
