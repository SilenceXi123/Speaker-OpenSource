from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.speaker_memory import SpeakerMemory
from app.services.speaker_memory import (
    build_program_memory_suggestions,
    derive_program_name,
    get_program_speaker_memory,
    list_program_lower_third_assets,
    upsert_program_lower_third_assets,
    upsert_program_speaker_memory,
)
from app.schemas.project import ProjectCreateRequest
from app.services.projects import create_project


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_derive_program_name_prefers_manual_name_and_falls_back_to_file_name() -> None:
    assert derive_program_name('  典籍里的中国  ', 'https://example.com/demo.mp4') == '典籍里的中国'
    assert derive_program_name(None, 'https://example.com/%E5%85%B8%E7%B1%8D%E9%87%8C%E7%9A%84%E4%B8%AD%E5%9B%BD_01.mp4') == '典籍里的中国 01'


def test_upsert_program_speaker_memory_merges_unique_names() -> None:
    session = _build_session()

    first = upsert_program_speaker_memory(session, '典籍里的中国', ['撒贝宁', '祝勇'])
    second = upsert_program_speaker_memory(session, '典籍里的中国', ['撒贝宁', '刘勃'])

    assert first is not None
    assert second is not None
    memory = get_program_speaker_memory(session, '典籍里的中国')
    assert memory is not None
    assert memory.program_name == '典籍里的中国'
    assert memory.speaker_names == ['刘勃', '撒贝宁', '祝勇']


def test_build_program_memory_suggestions_excludes_names_already_used_in_current_job() -> None:
    session = _build_session()
    memory = upsert_program_speaker_memory(session, '典籍里的中国', ['撒贝宁', '祝勇', '刘勃'])

    suggestions = build_program_memory_suggestions(memory, {'speaker_0': '撒贝宁'})

    assert suggestions == ['刘勃', '祝勇']


def test_program_lower_third_assets_are_reusable_and_deduped_by_speaker_name() -> None:
    session = _build_session()

    first = upsert_program_lower_third_assets(
        session,
        '中国好团队',
        [
            {
                'speaker_name': '刘权',
                'file_name': '刘权.png',
                'object_key': 'liuquan-old.png',
                'local_path': '/server/library/liuquan-old.png',
                'export_path': '/Volumes/节目A/刘权.png',
            }
        ],
    )
    second = upsert_program_lower_third_assets(
        session,
        '中国好团队',
        [
            {
                'speaker_name': '刘权',
                'file_name': '刘权.png',
                'object_key': 'liuquan-new.png',
                'local_path': '/server/library/liuquan-new.png',
                'export_path': '/Volumes/节目B/刘权.png',
            },
            {
                'speaker_name': '杜海涛',
                'file_name': '杜海涛.png',
                'object_key': 'duhaitao.png',
                'local_path': '/server/library/duhaitao.png',
            },
        ],
    )

    assert first is not None
    assert second is not None
    assets = list_program_lower_third_assets(session, '中国好团队')
    assert [asset['speaker_name'] for asset in assets] == ['刘权', '杜海涛']
    assert assets[0]['object_key'] == 'liuquan-new.png'
    assert assets[0]['export_path'] == '/Volumes/节目B/刘权.png'


def test_project_uuid_wins_over_mismatched_program_name_for_speaker_memory() -> None:
    session = _build_session()
    project_a = create_project(session, ProjectCreateRequest(name='UUID 项目 A'))
    project_b = create_project(session, ProjectCreateRequest(name='UUID 项目 B'))
    upsert_program_speaker_memory(
        session,
        project_a.name,
        ['项目 A 主持人'],
        project_id=project_a.id,
    )
    upsert_program_speaker_memory(
        session,
        project_b.name,
        ['项目 B 主持人'],
        project_id=project_b.id,
    )

    memory = get_program_speaker_memory(
        session,
        project_b.name,
        project_id=project_a.id,
    )

    assert memory is not None
    assert memory.project_id == project_a.id
    assert memory.speaker_names == ['项目 A 主持人']


def test_project_lower_third_libraries_are_isolated_by_uuid() -> None:
    session = _build_session()
    project_a = create_project(session, ProjectCreateRequest(name='人名条项目 A'))
    project_b = create_project(session, ProjectCreateRequest(name='人名条项目 B'))
    upsert_program_lower_third_assets(
        session,
        project_a.name,
        [{'speaker_name': '主持人 A', 'object_key': 'a.png'}],
        project_id=project_a.id,
    )
    upsert_program_lower_third_assets(
        session,
        project_b.name,
        [{'speaker_name': '主持人 B', 'object_key': 'b.png'}],
        project_id=project_b.id,
    )

    assets_a = list_program_lower_third_assets(
        session,
        project_b.name,
        project_id=project_a.id,
    )
    assets_b = list_program_lower_third_assets(
        session,
        project_a.name,
        project_id=project_b.id,
    )

    assert [item['speaker_name'] for item in assets_a] == ['主持人 A']
    assert [item['speaker_name'] for item in assets_b] == ['主持人 B']


def test_ambiguous_legacy_memory_is_not_claimed_by_uuid_project_reads() -> None:
    session = _build_session()
    project_a = create_project(session, ProjectCreateRequest(name='记忆歧义项目 A'))
    project_b = create_project(session, ProjectCreateRequest(name='记忆歧义项目 B'))
    project_a.legacy_program_key = '共享节目记忆'
    project_b.legacy_program_key = '共享节目记忆'
    session.add(
        SpeakerMemory(
            program_key='共享节目记忆',
            program_name='共享节目记忆',
            speaker_names=['不应泄漏的人名'],
        )
    )
    session.commit()

    memory_a = get_program_speaker_memory(session, '共享节目记忆', project_id=project_a.id)
    memory_b = get_program_speaker_memory(session, '共享节目记忆', project_id=project_b.id)

    assert memory_a is not None
    assert memory_b is not None
    assert memory_a.project_id == project_a.id
    assert memory_b.project_id == project_b.id
    assert '不应泄漏的人名' not in (memory_a.speaker_names or [])
    assert '不应泄漏的人名' not in (memory_b.speaker_names or [])


def test_conflicting_legacy_memory_fields_do_not_fallback_to_either_project() -> None:
    session = _build_session()
    project_a = create_project(session, ProjectCreateRequest(name='字段冲突项目 A'))
    project_b = create_project(session, ProjectCreateRequest(name='字段冲突项目 B'))
    for memory in list(session.query(SpeakerMemory).filter(SpeakerMemory.project_id.in_([project_a.id, project_b.id]))):
        session.delete(memory)
    session.commit()
    session.add(
        SpeakerMemory(
            program_key=project_a.name_key,
            program_name=project_b.name,
            speaker_names=['不可自动归属'],
        )
    )
    session.commit()

    assert get_program_speaker_memory(session, project_a.name, project_id=project_a.id) is None
    assert get_program_speaker_memory(session, project_b.name, project_id=project_b.id) is None


def test_duplicate_legacy_memories_are_not_read_or_claimed_by_uuid_project() -> None:
    session = _build_session()
    project = create_project(session, ProjectCreateRequest(name='重复记忆项目'))
    placeholder = get_program_speaker_memory(session, project.name, project_id=project.id)
    assert placeholder is not None
    session.delete(placeholder)
    session.commit()

    first = SpeakerMemory(
        program_key=project.name_key,
        program_name=project.name,
        speaker_names=['第一条旧姓名'],
    )
    second = SpeakerMemory(
        program_key='duplicate-memory-alternate-key',
        program_name=project.name,
        speaker_names=['第二条旧姓名'],
    )
    session.add_all([first, second])
    session.commit()

    assert get_program_speaker_memory(session, project.name, project_id=project.id) is None
    assert upsert_program_speaker_memory(
        session,
        project.name,
        ['不得自动合并'],
        project_id=project.id,
    ) is None

    session.refresh(first)
    session.refresh(second)
    assert first.project_id is None
    assert second.project_id is None
    assert first.speaker_names == ['第一条旧姓名']
    assert second.speaker_names == ['第二条旧姓名']
