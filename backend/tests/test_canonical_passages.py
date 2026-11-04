from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.services.canonical_passages import (
    add_canonical_variant,
    retrieve_canonical_corrections,
    upsert_canonical_passage,
)
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.runtime_lexicon import build_runtime_lexicon_bundle
from app.services.script_correction_candidates import (
    build_correction_candidates,
    build_script_snapshot,
)
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2


def _session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _jing_ye_si(session, *, source_type: str = 'manually_reviewed'):
    return upsert_canonical_passage(
        session,
        tenant_id='default',
        title='静夜思',
        author='李白',
        dynasty='唐',
        genre='五言绝句',
        canonical_text='床前明月光，疑是地上霜。举头望明月，低头思故乡。',
        source_type=source_type,
        source_ref='人工核定版本',
    )


def test_approved_authoritative_passage_returns_unique_homophone_correction() -> None:
    session = _session()
    passage = _jing_ye_si(session)
    session.commit()

    matches = retrieve_canonical_corrections(
        session,
        tenant_id='default',
        query_text='床前明月光疑是地上双举头望明月低头思故乡',
    )

    assert len(matches) == 1
    assert matches[0].source_text == '疑是地上双'
    assert matches[0].replacement_text == '疑是地上霜'
    assert matches[0].passage_id == passage.id
    assert matches[0].confidence >= 0.90
    assert matches[0].margin == 1.0


def test_approved_historical_variant_is_preserved_without_correction() -> None:
    session = _session()
    passage = _jing_ye_si(session)
    add_canonical_variant(
        session,
        passage_id=passage.id,
        variant_text='床前明月光，疑似地上霜。举头望明月，低头思故乡。',
        label='异文：是作似',
        source_type='authoritative_edition',
    )
    session.commit()

    matches = retrieve_canonical_corrections(
        session,
        tenant_id='default',
        query_text='床前明月光疑似地上霜举头望明月低头思故乡',
    )

    assert matches == ()


def test_ambiguous_same_pronunciation_passages_are_not_auto_corrected() -> None:
    session = _session()
    upsert_canonical_passage(
        session,
        tenant_id='default',
        title='版本甲',
        canonical_text='春眠不觉晓。',
        source_type='manually_reviewed',
    )
    upsert_canonical_passage(
        session,
        tenant_id='default',
        title='版本乙',
        canonical_text='春绵不绝晓。',
        source_type='manually_reviewed',
    )
    session.commit()

    matches = retrieve_canonical_corrections(
        session,
        tenant_id='default',
        query_text='春棉不决晓',
    )

    assert matches == ()


def test_unreviewed_community_corpus_cannot_drive_automatic_correction() -> None:
    session = _session()
    _jing_ye_si(session, source_type='community_dataset')
    session.commit()

    assert retrieve_canonical_corrections(
        session,
        tenant_id='default',
        query_text='疑是地上双',
    ) == ()


def test_classical_domain_bundle_feeds_canonical_candidate_into_same_pipeline() -> None:
    session = _session()
    _jing_ye_si(session)
    session.commit()
    query = '床前明月光疑是地上双举头望明月低头思故乡'
    bundle = build_runtime_lexicon_bundle(
        session,
        tenant_id='default',
        project_id='poetry-project',
        domain_id='classical_poetry',
        canonical_query_text=query,
    )
    assert len(bundle['canonical_corrections']) == 1

    words = ['床前', '明月光', '疑是', '地上', '双', '举头', '望明月', '低头', '思故乡']
    provider_words = []
    cursor = 0
    for word in words:
        provider_words.append({
            'text': word,
            'begin_time': cursor,
            'end_time': cursor + 100,
        })
        cursor += 100
    raw = build_raw_asr_snapshot(
        {'transcripts': [{'sentences': [{
            'speaker_id': 0,
            'begin_time': 0,
            'end_time': cursor,
            'text': ''.join(words),
            'words': provider_words,
        }]}]},
        provider_model='fun-asr',
        provider_task_id='canonical-test',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': cursor,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )
    candidates = build_correction_candidates(
        run_id='canonical-run',
        raw_snapshot=raw,
        semantic_artifact=build_semantic_utterances_v2(raw),
        script_snapshot=build_script_snapshot(''),
        runtime_lexicon_bundle=bundle,
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('疑是地上双', '疑是地上霜'),
    ]
    assert candidates[0].candidate_type == 'canonical_passage'
    assert any(
        value.startswith('canonical-passage:')
        for value in candidates[0].evidence_refs
    )


def test_broadcast_domain_never_loads_canonical_poetry_corrections() -> None:
    session = _session()
    _jing_ye_si(session)
    session.commit()

    bundle = build_runtime_lexicon_bundle(
        session,
        tenant_id='default',
        project_id='broadcast-project',
        domain_id='broadcast',
        canonical_query_text='疑是地上双',
    )

    assert bundle['canonical_corrections'] == []
