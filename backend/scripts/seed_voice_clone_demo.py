from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import Base, configure_sqlite_engine  # noqa: E402
from app.models.person import Person  # noqa: E402, F401
from app.models.project import Project  # noqa: E402
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: E402
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: E402
from app.models.voiceprint_sample import VoiceprintSample  # noqa: E402, F401


DEMO_NAMES = (
    '撒贝宁',
    '尼格买提',
    '王冰冰',
    '康辉',
    '朱广权',
    '董卿',
    '何炅',
    '汪涵',
    '鲁豫',
    '白岩松',
    '李思思',
    '周涛',
    '杨澜',
    '华少',
    '孟非',
    '陈鲁豫',
    '旁白男声',
    '旁白女声',
    '新闻男声',
    '新闻女声',
    '青年男声',
    '青年女声',
    '嘉宾通用',
)


@dataclass(frozen=True)
class VoiceCloneDemoSeedResult:
    project_id: str
    sample_count: int
    profile_count: int
    name_count: int
    unique_audio_count: int
    exact_duplicate_count: int
    media_root: str


def seed_voice_clone_demo(
    session: Session,
    media_root: Path,
    *,
    sample_count: int = 480,
    profile_count: int = 356,
    name_count: int = 23,
) -> VoiceCloneDemoSeedResult:
    if sample_count < 1 or profile_count < 1:
        raise ValueError('样本数和音色数必须大于 0')
    selected_names = DEMO_NAMES[:max(1, min(len(DEMO_NAMES), name_count))]
    project = session.scalar(
        select(Project).where(Project.name_key == 'voice-clone-scale-demo')
    )
    if project:
        existing_count = int(session.scalar(
            select(func.count(VoiceCloneSample.id)).where(
                VoiceCloneSample.project_id == project.id
            )
        ) or 0)
        if existing_count:
            raise RuntimeError('规模测试项目已存在，请使用新的空数据库')
    else:
        project = Project(
            name='声音复刻规模测试',
            name_key='voice-clone-scale-demo',
            legacy_program_key='voice-clone-scale-demo',
            status='active',
        )
        session.add(project)
        session.flush()

    media_root = Path(media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    unique_audio_count = min(sample_count, 296)
    duplicate_count = sample_count - unique_audio_count
    base_time = datetime(2026, 7, 27, 9, 0, 0)
    samples: list[VoiceCloneSample] = []
    for index in range(sample_count):
        content_index = (
            index
            if index < unique_audio_count
            else (index - unique_audio_count) % unique_audio_count
        )
        content = f'RIFF-DEMO-VOICE-{content_index:04d}'.encode()
        audio_path = media_root / f'sample-{index:04d}.wav'
        audio_path.write_bytes(content)
        if index < 34:
            readiness = 'rejected'
        elif index < 68:
            readiness = 'warning'
        else:
            readiness = 'ready'
        sample = VoiceCloneSample(
            display_name=selected_names[index % len(selected_names)],
            project_id=project.id,
            program_key=project.legacy_program_key,
            program_name=project.name,
            original_filename=audio_path.name,
            audio_local_path=str(audio_path),
            audio_public_url=f'https://example.test/demo/{audio_path.name}',
            content_sha256=hashlib.sha256(content).hexdigest(),
            file_size_bytes=len(content),
            duration_ms=12000 + (index % 9) * 500,
            sample_rate_hz=24000,
            channel_count=1,
            clone_readiness_status=readiness,
            clone_readiness_message=(
                '本地规模测试异常样本'
                if readiness != 'ready'
                else '本地规模测试可用样本'
            ),
            created_at=base_time - timedelta(minutes=index),
            last_used_at=(
                base_time - timedelta(hours=index % 96)
                if index < profile_count
                else None
            ),
        )
        samples.append(sample)
    session.add_all(samples)
    session.flush()

    profiles: list[VoiceCloneProfile] = []
    for index in range(profile_count):
        name = selected_names[index % len(selected_names)]
        if index % 71 == 0:
            status = 'creating'
        elif index % 53 == 0:
            status = 'failed'
        else:
            status = 'ready'
        profile = VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=samples[index % len(samples)].id,
            source_audio_local_path=samples[index % len(samples)].audio_local_path,
            program_key=project.legacy_program_key,
            program_name=project.name,
            provider='qwen',
            provider_model_family='qwen',
            target_model='qwen3-tts-vc-2026-01-22',
            voice_id=f'demo-voice-{index:04d}' if status == 'ready' else None,
            voice_name=name,
            status=status,
            provider_message='本地规模测试数据，不对应真实 Provider 资源',
            usage_program_keys=[],
            created_at=base_time - timedelta(hours=index),
            updated_at=base_time - timedelta(minutes=index * 3),
            last_used_at=(
                base_time - timedelta(minutes=index)
                if status == 'ready'
                else None
            ),
        )
        profiles.append(profile)
    session.add_all(profiles)
    session.commit()
    session.refresh(project)
    return VoiceCloneDemoSeedResult(
        project_id=project.id,
        sample_count=len(samples),
        profile_count=len(profiles),
        name_count=len(selected_names),
        unique_audio_count=unique_audio_count,
        exact_duplicate_count=duplicate_count,
        media_root=str(media_root),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='生成声音复刻 480/356 本地规模测试数据',
    )
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--media-root', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=480)
    parser.add_argument('--profiles', type=int, default=356)
    parser.add_argument('--names', type=int, default=23)
    parser.add_argument('--json', action='store_true')
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    engine = create_engine(
        args.database_url,
        connect_args={'check_same_thread': False},
    )
    configure_sqlite_engine(engine, enable_wal=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    try:
        with session_factory() as session:
            result = seed_voice_clone_demo(
                session,
                args.media_root,
                sample_count=args.samples,
                profile_count=args.profiles,
                name_count=args.names,
            )
    finally:
        engine.dispose()
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"已生成 {result.sample_count} 个样本、"
            f"{result.profile_count} 个音色、"
            f"{result.name_count} 个名称；"
            f"精确重复 {result.exact_duplicate_count} 条。"
        )
        print(f'项目 ID：{result.project_id}')
        print(f'媒体目录：{result.media_root}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
