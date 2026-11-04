from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_import import LexiconImport
from app.models.lexicon_occurrence import LexiconOccurrence
from app.models.lexicon_term import LexiconTerm
from app.models.lexicon_term_alias import LexiconTermAlias
from app.models.person import Person
from app.models.project import Project
from app.models.speaker_memory import SpeakerMemory
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.core.config import settings
from app.schemas.project import ProjectCreateRequest, ProjectUpdateRequest
from app.services.projects import (
    build_project_response,
    create_project,
    list_projects,
    migrate_legacy_projects,
    preview_legacy_project_migration,
    purge_project,
    set_project_status,
    update_project,
)
from scripts.migrate_v2_people_project_ids import inspect_people_project_migration
from scripts.migrate_v2_projects import _ensure_project_migration_schema
from scripts.migrate_v2_speaker_memories_project_ids import (
    inspect_speaker_memory_project_migration,
)


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _build_legacy_project_migration_engine():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE projects'))
        connection.execute(text('DROP INDEX ix_jobs_project_id'))
        connection.execute(text('ALTER TABLE jobs DROP COLUMN project_id'))
        connection.execute(text('DROP INDEX ix_people_project_id'))
        connection.execute(text('ALTER TABLE people DROP COLUMN project_id'))
        connection.execute(text('DROP TABLE speaker_memories'))
        connection.execute(text(
            'CREATE TABLE speaker_memories ('
            'id TEXT PRIMARY KEY, program_key TEXT UNIQUE, program_name TEXT, '
            'speaker_names JSON, lower_third_assets JSON, created_at TEXT, updated_at TEXT'
            ')'
        ))
        connection.execute(text(
            "INSERT INTO speaker_memories (id, program_key, program_name, speaker_names) VALUES "
            "('legacy-memory', '旧节目', '旧节目', '[]')"
        ))
    return engine


def _legacy_speaker_memory_snapshot(engine):
    with engine.connect() as connection:
        rows = connection.execute(text(
            'SELECT id, program_key, program_name, speaker_names, lower_third_assets, '
            'created_at, updated_at FROM speaker_memories ORDER BY id'
        )).all()
    return [tuple(row) for row in rows]


def test_project_uuid_survives_metadata_update_and_rename_is_deferred() -> None:
    session = _build_session()
    first = create_project(session, ProjectCreateRequest(name=' 中国好团队 '))
    original_id = first.id

    updated = update_project(session, first.id, ProjectUpdateRequest(owner_name='制作一组'))

    assert updated.id == original_id
    assert updated.owner_name == '制作一组'

    try:
        update_project(session, first.id, ProjectUpdateRequest(name='中国好团队第二季'))
    except ValueError as error:
        assert '暂不支持项目改名' in str(error)
    else:
        raise AssertionError('rename should be blocked until legacy assets are migrated')


def test_archived_project_is_hidden_from_default_list() -> None:
    session = _build_session()
    project = create_project(session, ProjectCreateRequest(name='待归档项目'))
    memory = session.query(SpeakerMemory).filter_by(program_key='待归档项目').one_or_none()
    assert memory is not None
    assert memory.project_id == project.id

    set_project_status(session, project.id, 'archived')

    assert list_projects(session) == []
    assert [item.id for item in list_projects(session, include_archived=True)] == [project.id]
    assert session.query(SpeakerMemory).filter_by(program_key='待归档项目').one_or_none() is None

    set_project_status(session, project.id, 'active')

    assert session.query(SpeakerMemory).filter_by(program_key='待归档项目').one_or_none() is not None


def test_project_response_counts_bound_and_unmigrated_legacy_jobs() -> None:
    session = _build_session()
    project = create_project(session, ProjectCreateRequest(name='计数项目'))
    session.add_all(
        [
            Job(source_url='https://example.com/a.wav', project_id=project.id, status='processing'),
            Job(source_url='https://example.com/b.wav', project_id=project.id, status='completed'),
            Job(source_url='https://example.com/legacy.wav', program_name='计数项目', status='completed'),
        ]
    )
    session.commit()

    response = build_project_response(session, project)

    assert response.job_count == 3
    assert response.active_job_count == 1


def test_project_with_jobs_cannot_be_archived() -> None:
    session = _build_session()
    project = create_project(session, ProjectCreateRequest(name='已有任务项目'))
    session.add(Job(source_url='https://example.com/a.wav', project_id=project.id, status='completed'))
    session.commit()

    try:
        set_project_status(session, project.id, 'archived')
    except RuntimeError as error:
        assert '只允许归档空项目' in str(error)
    else:
        raise AssertionError('project with jobs must not disappear from the client')


def test_project_with_unmigrated_legacy_job_cannot_be_archived() -> None:
    session = _build_session()
    project = create_project(session, ProjectCreateRequest(name='历史任务项目'))
    session.add(Job(source_url='https://example.com/legacy.wav', program_name='历史任务项目', status='completed'))
    session.commit()

    try:
        set_project_status(session, project.id, 'archived')
    except RuntimeError as error:
        assert '只允许归档空项目' in str(error)
    else:
        raise AssertionError('unmigrated legacy jobs must protect their formal project')


def test_project_with_legacy_voice_assets_cannot_be_archived() -> None:
    session = _build_session()
    project = create_project(session, ProjectCreateRequest(name='已有声纹项目'))
    session.add(Person(name='刘权', program_key='已有声纹项目', program_name='已有声纹项目'))
    session.commit()

    response = build_project_response(session, project)

    assert response.person_count == 1
    assert response.asset_count == 1
    try:
        set_project_status(session, project.id, 'archived')
    except RuntimeError as error:
        assert '词库或声音资产' in str(error)
    else:
        raise AssertionError('project with voice assets must not be archived')


def test_project_counts_formally_bound_and_unmigrated_people_once_each() -> None:
    session = _build_session()
    project = create_project(session, ProjectCreateRequest(name='人物计数项目'))
    session.add_all([
        Person(
            name='正式人物',
            project_id=project.id,
            program_key='人物计数项目',
            program_name='人物计数项目',
        ),
        Person(
            name='旧人物',
            program_key='人物计数项目',
            program_name='人物计数项目',
        ),
        Person(
            name='其他项目人物',
            program_key='其他项目',
            program_name='其他项目',
        ),
    ])
    session.commit()

    response = build_project_response(session, project)

    assert response.person_count == 2
    assert response.asset_count == 2


def test_ambiguous_legacy_person_is_not_counted_in_multiple_projects() -> None:
    session = _build_session()
    project_a = create_project(session, ProjectCreateRequest(name='歧义项目 A'))
    project_b = create_project(session, ProjectCreateRequest(name='歧义项目 B'))
    project_a.legacy_program_key = '共享旧项目'
    project_b.legacy_program_key = '共享旧项目'
    session.add(Person(name='待人工归属', program_key='共享旧项目', program_name='共享旧项目'))
    session.commit()

    assert build_project_response(session, project_a).person_count == 0
    assert build_project_response(session, project_b).person_count == 0


def test_clone_asset_counts_prefer_project_uuid_and_ignore_ambiguous_legacy_aliases() -> None:
    session = _build_session()
    project_a = create_project(session, ProjectCreateRequest(name='复刻计数项目 A'))
    project_b = create_project(session, ProjectCreateRequest(name='复刻计数项目 B'))
    project_a.legacy_program_key = '共享复刻旧项目'
    project_b.legacy_program_key = '共享复刻旧项目'
    session.add_all([
        VoiceCloneSample(
            display_name='A 的正式样本',
            project_id=project_a.id,
            program_key=project_b.name,
            program_name=project_b.name,
            original_filename='a.wav',
            audio_local_path='/tmp/a.wav',
        ),
        VoiceCloneProfile(
            project_id=project_a.id,
            program_key=project_b.name,
            program_name=project_b.name,
            target_model='qwen3-tts-vc-2026-01-22',
            voice_name='A 的正式音色',
            status='ready',
        ),
        VoiceCloneSample(
            display_name='待人工归属样本',
            program_key='共享复刻旧项目',
            program_name='共享复刻旧项目',
            original_filename='ambiguous.wav',
            audio_local_path='/tmp/ambiguous.wav',
        ),
        VoiceCloneProfile(
            program_key='共享复刻旧项目',
            program_name='共享复刻旧项目',
            target_model='cosyvoice-v3.5-plus',
            voice_name='待人工归属音色',
            status='ready',
        ),
    ])
    session.commit()

    response_a = build_project_response(session, project_a)
    response_b = build_project_response(session, project_b)

    assert response_a.sample_count == 1
    assert response_a.asset_count == 2
    assert response_b.sample_count == 0
    assert response_b.asset_count == 0


def test_ambiguous_legacy_speaker_memory_is_not_counted_or_deleted_by_projects() -> None:
    session = _build_session()
    project_a = create_project(session, ProjectCreateRequest(name='记忆项目 A'))
    project_b = create_project(session, ProjectCreateRequest(name='记忆项目 B'))
    project_a.legacy_program_key = '共享记忆旧项目'
    project_b.legacy_program_key = '共享记忆旧项目'
    shared_memory = SpeakerMemory(
        program_key='共享记忆旧项目',
        program_name='共享记忆旧项目',
        speaker_names=['待人工归属'],
    )
    session.add(shared_memory)
    session.commit()

    assert build_project_response(session, project_a).asset_count == 0
    assert build_project_response(session, project_b).asset_count == 0

    set_project_status(session, project_a.id, 'archived')

    assert session.get(SpeakerMemory, shared_memory.id) is not None


def test_purge_project_removes_owned_records_and_managed_files(tmp_path, monkeypatch) -> None:
    for setting_name, folder_name in (
        ('local_upload_dir', 'uploads'),
        ('local_audio_dir', 'audio'),
        ('local_render_dir', 'render'),
        ('local_media_library_dir', 'media'),
        ('local_lower_third_library_dir', 'lower-thirds'),
        ('local_voiceprint_library_dir', 'voiceprints'),
        ('local_voice_clone_library_dir', 'voice-cloning'),
    ):
        monkeypatch.setattr(settings, setting_name, str(tmp_path / folder_name))
    monkeypatch.setattr(settings, 'oss_region', '')

    session = _build_session()
    target = create_project(session, ProjectCreateRequest(name='待彻底清理'))
    survivor = create_project(session, ProjectCreateRequest(name='保留项目'))

    target_source = settings.media_library_dir_path / 'target.wav'
    survivor_source = settings.media_library_dir_path / 'survivor.wav'
    target_source.write_bytes(b'target')
    survivor_source.write_bytes(b'survivor')
    target_job = Job(
        source_url='https://example.com/target.wav',
        source_local_path=str(target_source),
        project_id=target.id,
        status='completed',
    )
    survivor_job = Job(
        source_url='https://example.com/survivor.wav',
        source_local_path=str(survivor_source),
        project_id=survivor.id,
        status='completed',
    )
    target_person = Person(name='待删人物', project_id=target.id)
    survivor_person = Person(name='保留人物', project_id=survivor.id)
    session.add_all([target_job, survivor_job, target_person, survivor_person])
    session.flush()

    voiceprint_path = settings.voiceprint_library_dir_path / 'target-voiceprint.wav'
    clone_path = settings.voice_clone_library_dir_path / 'target-clone.wav'
    output_path = settings.audio_dir_path / 'target-output.wav'
    backplate_path = (
        settings.lower_third_library_dir_path
        / 'projects'
        / target.id
        / 'subtitle-backplates'
        / 'target.png'
    )
    for path in (voiceprint_path, clone_path, output_path, backplate_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'owned')

    voiceprint = VoiceprintSample(
        person_id=target_person.id,
        original_filename='target-voiceprint.wav',
        audio_local_path=str(voiceprint_path),
        clone_audio_local_path=str(voiceprint_path),
    )
    clone_sample = VoiceCloneSample(
        display_name='待删复刻样本',
        project_id=target.id,
        original_filename='target-clone.wav',
        audio_local_path=str(clone_path),
    )
    session.add_all([voiceprint, clone_sample])
    session.flush()
    clone_profile = VoiceCloneProfile(
        project_id=target.id,
        person_id=target_person.id,
        source_sample_id=voiceprint.id,
        source_clone_sample_id=clone_sample.id,
        target_model='qwen3-tts-vc-2026-01-22',
        voice_name='待删音色',
        status='ready',
    )
    session.add(clone_profile)
    session.flush()
    clone_record = VoiceCloneSynthesisRecord(
        project_id=target.id,
        voice_clone_profile_id=clone_profile.id,
        person_id=target_person.id,
        target_model=clone_profile.target_model,
        input_text='测试',
        output_audio_local_path=str(output_path),
        status='completed',
    )
    session.add_all([
        clone_record,
        JobEvent(job_id=target_job.id, event_type='test'),
        VoiceprintMatch(
            job_id=target_job.id,
            speaker_key='speaker_0',
            person_id=target_person.id,
        ),
    ])

    term = LexiconTerm(
        tenant_id='default',
        scope_type='project',
        scope_id=target.id,
        normalized_term='待删词',
        display_text='待删词',
        term_type='custom',
        status='active',
        protection_level='hard',
        review_status='approved',
    )
    import_record = LexiconImport(
        tenant_id='default',
        project_id=target.id,
        source_name='待删台本.txt',
        source_type='script',
        parser_version='test',
        status='completed',
    )
    candidate = LexiconCandidate(
        tenant_id='default',
        project_id=target.id,
        normalized_term='候选词',
        display_text='候选词',
        term_type='custom',
        status='candidate',
        suggested_protection_level='soft',
    )
    session.add_all([term, import_record, candidate])
    session.flush()
    session.add_all([
        LexiconTermAlias(
            tenant_id='default',
            term_id=term.id,
            alias_text='待删别名',
            normalized_alias='待删别名',
        ),
        LexiconOccurrence(
            tenant_id='default',
            term_id=term.id,
            candidate_id=candidate.id,
            import_id=import_record.id,
            job_id=target_job.id,
            project_id=target.id,
            source_type='test',
        ),
    ])
    target.subtitle_backplate_assets = {
        'without_speaker': {'local_path': str(backplate_path), 'object_key': 'test/target.png'},
    }
    session.commit()

    deleted_ids = {
        'project': target.id,
        'job': target_job.id,
        'person': target_person.id,
        'voiceprint': voiceprint.id,
        'clone_sample': clone_sample.id,
        'clone_profile': clone_profile.id,
        'clone_record': clone_record.id,
        'term': term.id,
        'import': import_record.id,
        'candidate': candidate.id,
    }
    survivor_ids = {
        'project': survivor.id,
        'job': survivor_job.id,
        'person': survivor_person.id,
    }

    deleted_name = purge_project(session, deleted_ids['project'])

    assert deleted_name == '待彻底清理'
    assert session.get(Project, deleted_ids['project']) is None
    assert session.get(Job, deleted_ids['job']) is None
    assert session.get(Person, deleted_ids['person']) is None
    assert session.get(VoiceprintSample, deleted_ids['voiceprint']) is None
    assert session.get(VoiceCloneSample, deleted_ids['clone_sample']) is None
    assert session.get(VoiceCloneProfile, deleted_ids['clone_profile']) is None
    assert session.get(VoiceCloneSynthesisRecord, deleted_ids['clone_record']) is None
    assert session.get(LexiconTerm, deleted_ids['term']) is None
    assert session.get(LexiconImport, deleted_ids['import']) is None
    assert session.get(LexiconCandidate, deleted_ids['candidate']) is None
    assert not target_source.exists()
    assert not voiceprint_path.exists()
    assert not clone_path.exists()
    assert not output_path.exists()
    assert not backplate_path.exists()

    assert session.get(Project, survivor_ids['project']) is not None
    assert session.get(Job, survivor_ids['job']) is not None
    assert session.get(Person, survivor_ids['person']) is not None
    assert survivor_source.exists()


def test_legacy_project_migration_is_explicit_non_destructive_and_idempotent() -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        program_key='中国好团队',
        program_name='中国好团队',
        status='completed',
    )
    unresolved = Job(source_url='https://example.com/unresolved.wav', status='completed')
    session.add_all(
        [
            job,
            unresolved,
            Person(name='刘权', program_key='中国好团队', program_name='中国好团队'),
            SpeakerMemory(program_key='另一个节目', program_name='另一个节目', speaker_names=[]),
        ]
    )
    session.commit()

    preview = migrate_legacy_projects(session, apply=False)

    assert preview['mode'] == 'dry-run'
    assert preview['candidate_project_count'] == 2
    assert preview['created_project_count'] == 0
    assert preview['unresolved_job_count'] == 1
    assert session.query(Project).count() == 0
    assert job.project_id is None

    applied = migrate_legacy_projects(session, apply=True)
    session.refresh(job)

    assert applied['created_project_count'] == 2
    assert applied['mapped_job_count'] == 1
    assert job.project_id
    assert job.program_name == '中国好团队'
    assert job.program_key == '中国好团队'

    repeated = migrate_legacy_projects(session, apply=True)

    assert repeated['created_project_count'] == 0
    assert repeated['mapped_job_count'] == 0
    assert session.query(Project).count() == 2


def test_migration_preview_does_not_create_projects_table_or_columns() -> None:
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    with engine.begin() as connection:
        connection.execute(text(
            'CREATE TABLE jobs ('
            'id TEXT PRIMARY KEY, program_key TEXT, program_name TEXT, status TEXT'
            ')'
        ))
        connection.execute(
            text('INSERT INTO jobs (id, program_key, program_name, status) VALUES (:id, :key, :name, :status)'),
            {'id': 'legacy-job', 'key': '旧项目', 'name': '旧项目', 'status': 'completed'},
        )

    before_tables = set(inspect(engine).get_table_names())
    before_columns = {column['name'] for column in inspect(engine).get_columns('jobs')}
    preview = preview_legacy_project_migration(engine)

    assert preview['schema_mutated'] is False
    assert preview['planned_projects'] == [{'name': '旧项目', 'legacy_program_key': '旧项目'}]
    assert set(inspect(engine).get_table_names()) == before_tables
    assert {column['name'] for column in inspect(engine).get_columns('jobs')} == before_columns
    assert 'projects' not in inspect(engine).get_table_names()
    assert 'project_id' not in before_columns


def test_legacy_schema_migrates_projects_before_people_and_speaker_memories() -> None:
    engine = _build_legacy_project_migration_engine()
    before_tables = set(inspect(engine).get_table_names())
    before_job_columns = {column['name'] for column in inspect(engine).get_columns('jobs')}
    before_memory_columns = {
        column['name'] for column in inspect(engine).get_columns('speaker_memories')
    }
    before_people_columns = {
        column['name'] for column in inspect(engine).get_columns('people')
    }
    before_memory_indexes = inspect(engine).get_indexes('speaker_memories')
    before_memory_rows = _legacy_speaker_memory_snapshot(engine)

    preview = preview_legacy_project_migration(engine)

    assert preview['schema_mutated'] is False
    assert preview['planned_projects'] == [{'name': '旧节目', 'legacy_program_key': '旧节目'}]
    assert set(inspect(engine).get_table_names()) == before_tables
    assert {column['name'] for column in inspect(engine).get_columns('jobs')} == before_job_columns
    assert {
        column['name'] for column in inspect(engine).get_columns('speaker_memories')
    } == before_memory_columns
    assert inspect(engine).get_indexes('speaker_memories') == before_memory_indexes
    assert _legacy_speaker_memory_snapshot(engine) == before_memory_rows
    assert 'projects' not in before_tables
    assert 'project_id' not in before_job_columns
    assert 'project_id' not in before_people_columns
    assert 'project_id' not in before_memory_columns

    _ensure_project_migration_schema(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        projects_report = migrate_legacy_projects(session, apply=True)
    finally:
        session.close()

    assert projects_report['created_project_count'] == 1
    assert 'project_id' in {
        column['name'] for column in inspect(engine).get_columns('jobs')
    }
    assert {
        column['name'] for column in inspect(engine).get_columns('speaker_memories')
    } == before_memory_columns
    assert {
        column['name'] for column in inspect(engine).get_columns('people')
    } == before_people_columns
    assert _legacy_speaker_memory_snapshot(engine) == before_memory_rows

    people_report = inspect_people_project_migration(engine, apply=True)

    assert people_report['mode'] == 'apply'
    assert 'project_id' in {
        column['name'] for column in inspect(engine).get_columns('people')
    }
    assert {
        column['name'] for column in inspect(engine).get_columns('speaker_memories')
    } == before_memory_columns
    assert _legacy_speaker_memory_snapshot(engine) == before_memory_rows

    memory_report = inspect_speaker_memory_project_migration(engine, apply=True)

    assert memory_report['project_id_column_added'] is True
    assert memory_report['mapped_memory_count'] == 1
    assert _legacy_speaker_memory_snapshot(engine) == before_memory_rows
    with engine.connect() as connection:
        project_id = connection.execute(text(
            "SELECT id FROM projects WHERE legacy_program_key = '旧节目'"
        )).scalar_one()
        memory_project_id = connection.execute(text(
            "SELECT project_id FROM speaker_memories WHERE id = 'legacy-memory'"
        )).scalar_one()
    assert memory_project_id == project_id
