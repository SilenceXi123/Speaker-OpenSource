from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import os
import sys
import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base
from app.models.job import Job  # noqa: F401
from app.models.person import Person
from app.models.project import Project
from app.models.speaker_memory import SpeakerMemory
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord  # noqa: F401
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.voiceprint import PersonCreateRequest
from app.services.voiceprint_provider import VoiceprintEmbeddingResult, get_voiceprint_provider_status
from app.services import voiceprint_provider
from app.services.voiceprints import (
    build_person_response,
    build_sample_response,
    create_program_summary,
    create_person,
    delete_empty_program,
    delete_person,
    delete_voiceprint_sample,
    confirm_voiceprint_candidate,
    get_person,
    get_voiceprint_sample,
    list_program_summaries,
    list_person_samples,
    save_voiceprint_sample,
)


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_create_person_normalizes_program_name_and_aliases() -> None:
    session = _build_session()

    person = create_person(
        session,
        PersonCreateRequest(
            name='  撒贝宁  ',
            program_name='  典籍里的中国  ',
            aliases=[' 小撒 ', '  '],
            notes=' 主持人 ',
        ),
    )

    assert person.name == '撒贝宁'
    assert person.program_name == '典籍里的中国'
    assert person.program_key == '典籍里的中国'
    assert person.aliases == ['小撒']
    assert person.notes == '主持人'


def test_create_program_summary_persists_placeholder_memory() -> None:
    session = _build_session()

    summary = create_program_summary(session, '  典籍里的中国  ')

    assert summary.program_name == '典籍里的中国'
    assert summary.program_key == '典籍里的中国'

    memory = session.query(SpeakerMemory).filter(SpeakerMemory.program_key == '典籍里的中国').one()
    project = session.query(Project).filter(Project.name_key == '典籍里的中国').one()
    assert memory.program_name == '典籍里的中国'
    assert memory.speaker_names == []
    assert memory.project_id == project.id


def test_list_program_summaries_merges_people_jobs_and_memory() -> None:
    session = _build_session()
    create_person(session, PersonCreateRequest(name='撒贝宁', program_name='典籍里的中国'))
    create_program_summary(session, '经典咏流传')
    session.add(
        Job(
            id='job-1',
            kind='media',
            status='completed',
            source_url='https://example.com/demo.mp4',
            program_key='你好生活',
            program_name='你好生活',
        )
    )
    session.commit()

    summaries = list_program_summaries(session)

    assert [item.program_name for item in summaries] == ['你好生活', '典籍里的中国', '经典咏流传']


def test_delete_empty_program_removes_placeholder_memory() -> None:
    session = _build_session()
    create_program_summary(session, '临时测试项目')

    response = delete_empty_program(session, '临时测试项目')

    assert response.deleted is True
    assert response.program_key == '临时测试项目'
    assert session.query(SpeakerMemory).filter(SpeakerMemory.program_key == '临时测试项目').count() == 0


def test_delete_empty_program_rejects_project_with_people() -> None:
    session = _build_session()
    create_program_summary(session, '有人物项目')
    create_person(session, PersonCreateRequest(name='测试人物', program_name='有人物项目'))

    try:
        delete_empty_program(session, '有人物项目')
    except RuntimeError as error:
        assert str(error) == '该项目已被任务或声纹数据使用，暂不允许删除'
    else:
        raise AssertionError('expected RuntimeError')


def test_delete_empty_program_rejects_project_with_jobs() -> None:
    session = _build_session()
    create_program_summary(session, '有任务项目')
    session.add(
        Job(
            id='job-delete-check',
            kind='media',
            status='completed',
            source_url='https://example.com/demo.mp4',
            program_key='有任务项目',
            program_name='有任务项目',
        )
    )
    session.commit()

    try:
        delete_empty_program(session, '有任务项目')
    except RuntimeError as error:
        assert str(error) == '该项目已被任务或声纹数据使用，暂不允许删除'
    else:
        raise AssertionError('expected RuntimeError')


def test_delete_empty_program_rejects_saved_speaker_names_and_lower_thirds() -> None:
    session = _build_session()
    create_program_summary(session, '有记忆资产项目')
    memory = session.query(SpeakerMemory).filter_by(program_key='有记忆资产项目').one()
    memory.speaker_names = ['主持人']
    memory.lower_third_assets = [{'speaker_name': '主持人', 'object_key': 'host.png'}]
    session.commit()

    try:
        delete_empty_program(session, '有记忆资产项目')
    except RuntimeError as error:
        assert '说话人姓名或人名条' in str(error)
    else:
        raise AssertionError('project memory assets must protect the project from deletion')


def test_delete_empty_program_raises_when_missing() -> None:
    session = _build_session()

    try:
        delete_empty_program(session, '不存在的项目')
    except LookupError as error:
        assert str(error) == '项目不存在'
    else:
        raise AssertionError('expected LookupError')


def test_save_voiceprint_sample_marks_pending_when_provider_unavailable(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='撒贝宁'))

    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 12.5)
    monkeypatch.setattr('app.services.voiceprints.extract_voiceprint_embedding', lambda _path: (_ for _ in ()).throw(RuntimeError('provider unavailable')))

    with TemporaryDirectory() as tmp_dir:
        sample_path = Path(tmp_dir) / 'demo.wav'
        sample_path.write_bytes(b'RIFFdemo')
        sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')

    assert sample.embedding_status == 'pending_provider'
    assert sample.embedding is None
    assert 'provider unavailable' in (sample.provider_message or '')
    assert sample.usage_types == ['voiceprint', 'voice_cloning']
    assert sample.clone_readiness_status in {'ready', 'warning', 'rejected'}


def test_save_voiceprint_sample_marks_ready_when_embedding_is_extracted(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='祝勇'))

    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 8.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2, 0.3],
            provider_name='3d_speaker_cli',
            provider_model_id='iic/speech_campplus_sv_zh-cn_16k-common',
            provider_message='embedding 提取成功',
        ),
    )

    with TemporaryDirectory() as tmp_dir:
        sample_path = Path(tmp_dir) / 'demo.wav'
        sample_path.write_bytes(b'RIFFdemo')
        sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')

    assert sample.embedding_status == 'ready'
    assert sample.embedding == [0.1, 0.2, 0.3]
    assert sample.provider_name == '3d_speaker_cli'


def test_build_person_response_counts_samples(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='刘勃'))

    monkeypatch_values = [
        VoiceprintEmbeddingResult(
            embedding=[0.1],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
        RuntimeError('provider unavailable'),
    ]

    def _extract(_path):
        value = monkeypatch_values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 5.0)
    monkeypatch.setattr('app.services.voiceprints.extract_voiceprint_embedding', _extract)

    with TemporaryDirectory() as tmp_dir:
        for index in range(2):
            sample_path = Path(tmp_dir) / f'demo-{index}.wav'
            sample_path.write_bytes(b'RIFFdemo')
            save_voiceprint_sample(session, person, sample_path, f'demo-{index}.wav')

    response = build_person_response(person, list_person_samples(session, person.id))

    assert response.sample_count == 2
    assert response.ready_sample_count == 1


def test_build_sample_response_exposes_auto_learning_metadata() -> None:
    session = _build_session()
    person = Person(name='刘权', program_name='中国好团队', program_key='中国好团队')
    session.add(person)
    session.commit()
    session.refresh(person)
    sample = VoiceprintSample(
        person_id=person.id,
        original_filename='auto-刘权.wav',
        audio_local_path='/tmp/auto-刘权.wav',
        duration_ms=4000,
        sample_rate_hz=16000,
        channel_count=1,
        embedding_status='ready',
        source_type='auto_candidate',
        source_job_id='job-1',
        source_speaker_key='speaker_0',
        source_start_ms=1200,
        source_end_ms=5200,
        confidence_score=0.81,
        review_status='candidate',
        duplicate_of_sample_id=None,
    )
    session.add(sample)
    session.commit()
    session.refresh(sample)

    response = build_sample_response(sample)

    assert response.source_type == 'auto_candidate'
    assert response.review_status == 'candidate'
    assert response.source_job_id == 'job-1'
    assert response.source_speaker_key == 'speaker_0'
    assert response.source_start_ms == 1200
    assert response.source_end_ms == 5200
    assert response.confidence_score == 0.81
    assert response.duplicate_of_sample_id is None


def test_confirm_voiceprint_candidate_promotes_candidate_sample() -> None:
    session = _build_session()
    person = Person(name='刘权', program_name='中国好团队', program_key='中国好团队')
    session.add(person)
    session.commit()
    sample = VoiceprintSample(
        person_id=person.id,
        original_filename='auto-刘权.wav',
        audio_local_path='/tmp/auto-刘权.wav',
        duration_ms=4000,
        sample_rate_hz=16000,
        channel_count=1,
        usage_types=['voiceprint'],
        embedding_status='ready',
        source_type='auto_candidate',
        review_status='candidate',
    )
    session.add(sample)
    session.commit()
    session.refresh(sample)

    confirmed = confirm_voiceprint_candidate(session, sample)

    assert confirmed.source_type == 'auto_confirmed'
    assert confirmed.review_status == 'confirmed'


def test_save_voiceprint_sample_sets_clone_readiness_and_public_url(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='测试人物'))

    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 15.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    monkeypatch.setattr('app.services.voiceprints.ensure_sample_public_url', lambda _path: 'https://example.com/sample.wav')

    with TemporaryDirectory() as tmp_dir:
        sample_path = Path(tmp_dir) / 'demo.wav'
        sample_path.write_bytes(b'RIFFdemo')
        sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')

    assert sample.clone_readiness_status == 'ready'
    assert sample.audio_public_url == 'https://example.com/sample.wav'
    assert sample.usage_types == ['voiceprint', 'voice_cloning']


def test_save_voiceprint_sample_keeps_separate_clone_reference(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='复刻样本人物'))

    monkeypatch.setattr(settings, 'voice_cloning_sample_target_rate_hz', 24000, raising=False)
    monkeypatch.setattr(settings, 'voice_cloning_sample_channel_count', 1, raising=False)
    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 18.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    monkeypatch.setattr(
        'app.services.voiceprints.ensure_sample_public_url',
        lambda path: f'https://example.com/{Path(path).name}',
    )

    with TemporaryDirectory() as tmp_dir:
        voiceprint_path = Path(tmp_dir) / 'demo-16k.wav'
        clone_path = Path(tmp_dir) / 'demo-24k.wav'
        voiceprint_path.write_bytes(b'RIFF16K')
        clone_path.write_bytes(b'RIFF24K')

        sample = save_voiceprint_sample(
            session,
            person,
            voiceprint_path,
            'demo.wav',
            clone_audio_local_path=clone_path,
        )
        response = build_sample_response(sample)

    assert sample.audio_local_path == str(voiceprint_path)
    assert sample.clone_audio_local_path == str(clone_path)
    assert sample.audio_public_url == 'https://example.com/demo-24k.wav'
    assert sample.clone_audio_public_url == 'https://example.com/demo-24k.wav'
    assert sample.sample_rate_hz == 24000
    assert sample.channel_count == 1
    assert sample.clone_sample_rate_hz == 24000
    assert sample.clone_channel_count == 1
    assert response.clone_audio_public_url == 'https://example.com/demo-24k.wav'


def test_delete_voiceprint_sample_removes_clone_reference_file(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='删除复刻样本人物'))

    monkeypatch.setattr(settings, 'voice_cloning_sample_target_rate_hz', 24000, raising=False)
    monkeypatch.setattr(settings, 'voice_cloning_sample_channel_count', 1, raising=False)
    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 12.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )

    with TemporaryDirectory() as tmp_dir:
        voiceprint_path = Path(tmp_dir) / 'demo-16k.wav'
        clone_path = Path(tmp_dir) / 'demo-24k.wav'
        voiceprint_path.write_bytes(b'RIFF16K')
        clone_path.write_bytes(b'RIFF24K')
        sample = save_voiceprint_sample(
            session,
            person,
            voiceprint_path,
            'demo.wav',
            clone_audio_local_path=clone_path,
        )

        delete_voiceprint_sample(session, sample)

        assert get_voiceprint_sample(session, sample.id) is None
        assert not voiceprint_path.exists()
        assert not clone_path.exists()


def test_delete_voiceprint_sample_removes_record_and_local_file(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='辉子'))

    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 3.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )

    with TemporaryDirectory() as tmp_dir:
        sample_path = Path(tmp_dir) / 'demo.wav'
        sample_path.write_bytes(b'RIFFdemo')
        sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')
        saved_path = Path(sample.audio_local_path)

        assert saved_path.exists()

        delete_voiceprint_sample(session, sample)

        assert get_voiceprint_sample(session, sample.id) is None
        assert not saved_path.exists()


def test_delete_person_removes_samples_files_and_related_matches(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='测试人物', program_name='联调节目'))

    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 3.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )

    with TemporaryDirectory() as tmp_dir:
        sample_path = Path(tmp_dir) / 'demo.wav'
        sample_path.write_bytes(b'RIFFdemo')
        sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')
        saved_path = Path(sample.audio_local_path)

        session.add(VoiceprintMatch(job_id='job-1', speaker_key='speaker_0', person_id=person.id, status='needs_review'))
        session.commit()

        assert saved_path.exists()

        delete_person(session, person)

        assert get_person(session, person.id) is None
        assert get_voiceprint_sample(session, sample.id) is None
        assert session.query(VoiceprintMatch).filter(VoiceprintMatch.person_id == person.id).count() == 0
        assert not saved_path.exists()


def test_get_voiceprint_provider_status_defaults_to_disabled() -> None:
    status = get_voiceprint_provider_status()
    assert status.provider in {'disabled', '3d_speaker_cli', ''}


def test_get_voiceprint_provider_status_reports_unsupported_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'voiceprint_provider', '3d_speaker_cli')
    monkeypatch.setattr(settings, 'voiceprint_model_id', 'unsupported/model')
    with TemporaryDirectory() as tmp_dir:
        speaker_dir = Path(tmp_dir)
        infer_script = speaker_dir / 'speakerlab' / 'bin' / 'infer_sv.py'
        infer_script.parent.mkdir(parents=True, exist_ok=True)
        infer_script.write_text('#!/usr/bin/env python3\n', encoding='utf-8')
        monkeypatch.setattr(settings, 'voiceprint_3d_speaker_dir', str(speaker_dir))
        status = get_voiceprint_provider_status()

    assert status.available is False
    assert '仅支持官方 infer_sv.py 已覆盖的常用声纹模型' in status.message


def test_get_voiceprint_provider_status_ready_when_runtime_available(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'voiceprint_provider', '3d_speaker_cli')
    monkeypatch.setattr(settings, 'voiceprint_model_id', 'iic/speech_campplus_sv_zh-cn_16k-common')
    with TemporaryDirectory() as tmp_dir:
        speaker_dir = Path(tmp_dir)
        infer_script = speaker_dir / 'speakerlab' / 'bin' / 'infer_sv.py'
        infer_script.parent.mkdir(parents=True, exist_ok=True)
        infer_script.write_text('#!/usr/bin/env python3\n', encoding='utf-8')
        monkeypatch.setattr(settings, 'voiceprint_3d_speaker_dir', str(speaker_dir))
        monkeypatch.setattr('app.services.voiceprint_provider._load_3d_speaker_runtime', lambda: {'FBank': object, 'dynamic_import': object})
        status = get_voiceprint_provider_status()

    assert status.available is True
    assert '官方本地运行时已就绪' in status.message


def test_voiceprint_model_extraction_is_serialized(monkeypatch) -> None:
    first_started = Event()
    second_started = Event()
    release_first = Event()
    counter_lock = Lock()
    started_count = 0

    monkeypatch.setattr(
        voiceprint_provider,
        'get_voiceprint_provider_status',
        lambda: voiceprint_provider.VoiceprintProviderStatus(
            provider='3d_speaker_cli',
            model_id='model',
            available=True,
            message='ok',
        ),
    )

    def fake_extract(_path: Path) -> list[float]:
        nonlocal started_count
        with counter_lock:
            started_count += 1
            if started_count == 1:
                first_started.set()
            else:
                second_started.set()
        release_first.wait(timeout=1)
        return [1.0]

    monkeypatch.setattr(voiceprint_provider, '_extract_embedding_with_3d_speaker', fake_extract)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(voiceprint_provider.extract_voiceprint_embedding, Path('first.wav'))
        assert first_started.wait(timeout=1)
        second = executor.submit(voiceprint_provider.extract_voiceprint_embedding, Path('second.wav'))
        overlapped = second_started.wait(timeout=0.1)
        release_first.set()
        first.result(timeout=1)
        second.result(timeout=1)

    assert overlapped is False


def test_get_modelscope_cache_dir_defaults_to_project_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv('MODELSCOPE_CACHE', raising=False)
    monkeypatch.setattr(settings, 'voiceprint_model_cache_dir', str(tmp_path / 'models'))

    from app.services.voiceprint_provider import _get_modelscope_cache_dir

    cache_dir = _get_modelscope_cache_dir()

    assert cache_dir == tmp_path / 'models' / 'modelscope'
    assert cache_dir.exists()
    assert os.environ['MODELSCOPE_CACHE'] == str(cache_dir)


def test_prepare_local_model_path_uses_downloaded_model_directly(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, 'voiceprint_model_id', 'iic/speech_campplus_sv_zh-cn_16k-common')
    monkeypatch.setattr(settings, 'voiceprint_model_cache_dir', str(tmp_path / 'models'))

    downloaded_dir = tmp_path / 'downloaded-model'
    downloaded_dir.mkdir(parents=True, exist_ok=True)
    downloaded_path = downloaded_dir / 'campplus_cn_common.bin'
    downloaded_path.write_bytes(b'model')

    snapshot_module = types.ModuleType('modelscope.hub.snapshot_download')
    snapshot_module.snapshot_download = lambda *_args, **_kwargs: str(downloaded_dir)
    monkeypatch.setitem(sys.modules, 'modelscope.hub.snapshot_download', snapshot_module)

    from app.services.voiceprint_provider import _prepare_local_model_path

    model_path = _prepare_local_model_path(
        {
            'revision': 'v1.0.0',
            'model_pt': 'campplus_cn_common.bin',
        }
    )

    assert model_path == downloaded_path
