from app.schemas.subtitle import SubtitleCue
from app.models.job import Job
from app.services.jobs import save_transcription_result
from app.services.subtitle_cue_validator import validate_subtitle_cues
from app.services.subtitles import build_subtitle_cues


def _runtime_bundle() -> dict:
    return {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'term_set_hash': 'test-runtime-lexicon',
        'terms': [
            {
                'display_text': '北京元空智能科技有限公司',
                'normalized_term': '北京元空智能科技有限公司',
                'term_type': 'organization',
                'protection_level': 'hard',
            },
            {
                'display_text': '聪明',
                'normalized_term': '聪明',
                'term_type': 'concept',
                'protection_level': 'strong',
            },
            {
                'display_text': '创业',
                'normalized_term': '创业',
                'term_type': 'concept',
                'protection_level': 'soft',
            },
        ],
    }


def _ascii_runtime_bundle() -> dict:
    return {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'term_set_hash': 'test-ascii-runtime-lexicon',
        'terms': [
            {
                'display_text': 'OpenAI',
                'normalized_term': 'OpenAI',
                'term_type': 'organization',
                'protection_level': 'hard',
            },
            {
                'display_text': 'ChatGPT',
                'normalized_term': 'ChatGPT',
                'term_type': 'product',
                'protection_level': 'strong',
            },
        ],
    }


def _cue(index: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_key='speaker_0',
        speaker_name='主持人',
        full_text=f'主持人：{text}',
        clean_text=text,
    )


def test_runtime_lexicon_prevents_hard_term_split_in_cue_builder() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 5000,
            'text': '我们今天介绍北京元空智能科技有限公司聪明创业项目',
        }
    ]

    cues = build_subtitle_cues(segments, {}, runtime_lexicon_bundle=_runtime_bundle())

    hard_term = '北京元空智能科技有限公司'
    assert any(hard_term in cue.clean_text for cue in cues)
    assert hard_term in ''.join(cue.clean_text for cue in cues)


def test_runtime_lexicon_overlong_sentence_term_does_not_force_overlong_cue() -> None:
    text = '社区学院看一看看他们是如何打通邻里之间'
    bundle = {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'term_set_hash': 'overlong-sentence-term',
        'terms': [
            {
                'display_text': text,
                'normalized_term': text,
                'term_type': 'term',
                'protection_level': 'hard',
            }
        ],
    }
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 5000,
            'text': text,
        }
    ]

    cues = build_subtitle_cues(segments, {}, runtime_lexicon_bundle=bundle)
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == text
    assert len(cue_texts) >= 2
    assert all(len(cue.clean_text) <= 16 for cue in cues)


def test_cue_validator_blocks_split_hard_term_and_flags_strong_term() -> None:
    bundle = _runtime_bundle()

    hard_report = validate_subtitle_cues(
        [
            _cue(1, 0, 1000, '这里是北京元空智能科技有限公'),
            _cue(2, 1000, 2000, '司创业项目'),
        ],
        runtime_lexicon_bundle=bundle,
    )

    assert any(
        issue.code == 'lexicon_hard_term_split'
        and issue.severity == 'P0'
        and issue.term == '北京元空智能科技有限公司'
        for issue in hard_report.issues
    )

    strong_report = validate_subtitle_cues(
        [
            _cue(1, 0, 1000, '这是一个很聪'),
            _cue(2, 1000, 2000, '明的方案'),
        ],
        runtime_lexicon_bundle=bundle,
    )

    assert any(
        issue.code == 'lexicon_strong_term_split'
        and issue.severity == 'P1'
        and issue.term == '聪明'
        for issue in strong_report.issues
    )

    allowed_report = validate_subtitle_cues(
        [
            _cue(1, 0, 1000, '这是一个很聪'),
            _cue(2, 1000, 2000, '明的方案'),
        ],
        runtime_lexicon_bundle=bundle,
        allow_strong_term_split=True,
    )

    assert not any(issue.code == 'lexicon_strong_term_split' for issue in allowed_report.issues)

    soft_report = validate_subtitle_cues(
        [
            _cue(1, 0, 1000, '大家一起创'),
            _cue(2, 1000, 2000, '业吧'),
        ],
        runtime_lexicon_bundle=bundle,
    )

    assert not any(issue.severity == 'P0' and issue.term == '创业' for issue in soft_report.issues)


def test_cue_validator_checks_ascii_runtime_terms_before_ascii_boundary_skip() -> None:
    bundle = _ascii_runtime_bundle()

    hard_report = validate_subtitle_cues(
        [
            _cue(1, 0, 1000, 'Open'),
            _cue(2, 1000, 2000, 'AI rocks'),
        ],
        runtime_lexicon_bundle=bundle,
    )

    assert any(
        issue.code == 'lexicon_hard_term_split'
        and issue.severity == 'P0'
        and issue.term == 'OpenAI'
        for issue in hard_report.issues
    )

    strong_report = validate_subtitle_cues(
        [
            _cue(1, 0, 1000, 'Chat'),
            _cue(2, 1000, 2000, 'GPT helps'),
        ],
        runtime_lexicon_bundle=bundle,
    )

    assert any(
        issue.code == 'lexicon_strong_term_split'
        and issue.severity == 'P1'
        and issue.term == 'ChatGPT'
        for issue in strong_report.issues
    )


def test_transcription_runtime_lexicon_bundle_failure_is_observable(monkeypatch) -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = Job(
            kind='transcription',
            source_url='https://example.com/source.wav',
            status='processing',
            export_frame_rate='25',
            subtitle_strategy='enhanced',
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        monkeypatch.setattr(
            'app.services.jobs.build_runtime_lexicon_bundle',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('lexicon unavailable')),
        )
        monkeypatch.setattr(
            'app.services.jobs.apply_subtitle_strategy',
            lambda _job, segments, **_kwargs: (segments, False, None),
        )

        saved = save_transcription_result(
            session=session,
            job_id=job.id,
            transcription_url='https://example.com/transcription.json',
            raw_payload={
                'transcripts': [
                    {
                        'sentences': [
                            {
                                'sentence_id': 1,
                                'speaker_id': 0,
                                'begin_time': 0,
                                'end_time': 1000,
                                'text': 'hello',
                                'words': [{'text': 'hello', 'begin_time': 0, 'end_time': 1000}],
                            }
                        ]
                    }
                ]
            },
        )

        warning = saved.pipeline_artifacts['runtime_lexicon_bundle_snapshot']['load_warning']
        assert saved.status == 'completed'
        assert saved.processing_metrics['runtime_lexicon_bundle_load_failed'] == 1.0
        assert saved.processing_metrics['runtime_lexicon_bundle_error'] == 'lexicon unavailable'
        assert warning['code'] == 'runtime_lexicon_bundle_load_failed'
        assert warning['message'] == 'lexicon unavailable'
    finally:
        session.close()
