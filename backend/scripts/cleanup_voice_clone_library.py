from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.session import configure_sqlite_engine  # noqa: E402
from app.services.voice_clone_cleanup import (  # noqa: E402
    apply_voice_clone_cleanup_plan,
    build_voice_clone_cleanup_plan,
    drain_voice_clone_deletion_tasks,
    load_voice_clone_cleanup_plan,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='预览或执行声音复刻失效资产、精确重复和同名上限清理',
    )
    parser.add_argument(
        '--database-url',
        default=settings.sqlite_url,
        help='目标 SQLite URL；默认读取当前后端配置',
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        '--apply',
        action='store_true',
        help='确认执行数据库硬清理并创建资产删除任务；省略时只预览',
    )
    parser.add_argument(
        '--reset-all', action='store_true',
        help='全量清空识别声纹、匹配结果、声音身份（含别名和向量）、所有审核、复刻样本、音色和生成记录及关联音频；保留人物、项目和字幕',
    )
    parser.add_argument(
        '--no-backup', action='store_true',
        help='仅配合 --reset-all --apply：明确不保留含旧声音数据的数据库备份',
    )
    mode.add_argument(
        '--run-deletion-tasks',
        action='store_true',
        help='执行一批到期的 Provider、OSS 和本地媒体删除重试',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='输出机器可读 JSON',
    )
    parser.add_argument(
        '--archive-dir',
        type=Path,
        help='去重/同名裁减执行前保存待删除媒体的全新归档目录',
    )
    parser.add_argument(
        '--output-plan',
        type=Path,
        help='只预览时将版本化清理计划保存到此路径',
    )
    parser.add_argument(
        '--plan',
        type=Path,
        help='--apply 时加载此前保存的未修改计划',
    )
    parser.add_argument(
        '--backup-path',
        type=Path,
        help='--apply 时在任何清理变更前写入 SQLite 备份',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='--run-deletion-tasks 单次最多处理的任务数；默认 20',
    )
    return parser


def _format_counts(values: list[str]) -> str:
    counts = Counter(values)
    return '，'.join(
        f'{value}={counts[value]}' for value in sorted(counts)
    )


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.reset_all and args.apply and not args.no_backup:
        parser.error('--reset-all --apply 必须显式指定 --no-backup，且不保留媒体归档')
    if args.no_backup and (not args.reset_all or not args.apply or args.backup_path or args.archive_dir):
        parser.error('--no-backup 仅允许配合 --reset-all --apply，不能同时备份或归档')
    if args.apply and (args.plan is None or (args.backup_path is None and not args.no_backup)):
        parser.error('--apply 必须指定 --plan 和 --backup-path；全量重置可显式选择 --no-backup')
    if args.run_deletion_tasks and any((
        args.plan,
        args.output_plan,
        args.backup_path,
        args.archive_dir,
        args.reset_all,
        args.no_backup,
    )):
        parser.error(
            '--run-deletion-tasks 不能与 --plan、--output-plan、'
            '--backup-path 或 --archive-dir 同时使用'
        )
    if args.run_deletion_tasks and args.limit < 1:
        parser.error('--run-deletion-tasks 的 --limit 必须大于 0')
    engine = create_engine(
        str(args.database_url),
        connect_args={'check_same_thread': False},
    )
    configure_sqlite_engine(engine, enable_wal=False)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    try:
        with session_factory() as session:
            if args.run_deletion_tasks:
                deletion_tasks = drain_voice_clone_deletion_tasks(
                    session,
                    limit=args.limit,
                )
                payload = {
                    'mode': 'deletion-tasks',
                    'deletion_tasks': deletion_tasks.as_dict(),
                }
                if args.json:
                    print(json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ))
                else:
                    print(
                        f'删除任务：处理 {deletion_tasks.attempted_count} 条，'
                        f'完成 {deletion_tasks.completed_count} 条，'
                        f'失败 {deletion_tasks.failed_count} 条。'
                    )
                return 0
            plan = (
                load_voice_clone_cleanup_plan(args.plan)
                if args.apply
                else build_voice_clone_cleanup_plan(session, reset_all=args.reset_all)
            )
            if plan.reset_all != args.reset_all:
                parser.error('计划的全量重置模式与 --reset-all 不一致')
            if not args.apply and args.output_plan is not None:
                args.output_plan.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True)
                if args.reset_all:
                    # A preview must never overwrite media, the database, or another plan.
                    descriptor = os.open(args.output_plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                        handle.write(payload)
                else:
                    args.output_plan.write_text(payload, encoding='utf-8')

            result = (
                apply_voice_clone_cleanup_plan(
                    session,
                    plan,
                    backup_path=args.backup_path,
                    plan_path=args.plan,
                    archive_dir=args.archive_dir,
                    no_backup=args.no_backup,
                )
                if args.apply
                else None
            )
    finally:
        engine.dispose()

    payload = {
        'mode': 'apply' if args.apply else 'dry-run',
        'plan': plan.as_dict(),
        'result': result.as_dict() if result else None,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif plan.reset_all:
        print(f"全量重置：{'已执行' if args.apply else '只预览'}；" + '，'.join(
            f'{table}={count}' for table, count in plan.reset_table_counts
        ))
        print(f'资产删除目标 {len(plan.deletion_targets)} 条；人物、项目和字幕保留。')
        if args.apply:
            print('数据库已清空；关联资产需运行 --run-deletion-tasks 完成删除，失败任务保留待重试。')
    else:
        print(
            f"模式：{'执行' if args.apply else '只预览'}；"
            f"扫描 {plan.scanned_sample_count} 条，"
            f"发现 {len(plan.duplicate_groups)} 组 / "
            f"{plan.exact_duplicate_sample_count} 条精确重复，"
            f"另有 {plan.name_cap_sample_count} 条同名超额样本，"
            f"预计可释放 {plan.releasable_bytes} 字节。"
        )
        print(
            f'失效音色：{len(plan.invalid_profiles)} 条'
            f'（{_format_counts([entry.reason for entry in plan.invalid_profiles])}）'
        )
        print(
            f'失效样本：{len(plan.invalid_samples)} 条'
            f'（{_format_counts([entry.reason for entry in plan.invalid_samples])}）'
        )
        print(
            f'生成记录：解绑并保留 {len(plan.detach_record_ids)} 条，'
            '删除失败/处理中且无输出 '
            f'{len(plan.delete_record_ids)} 条。'
        )
        print(
            f'删除目标：{len(plan.deletion_targets)} 条'
            f'（{_format_counts([target.kind for target in plan.deletion_targets])}）'
        )
        print(f'计划指纹：{plan.plan_fingerprint}')
        print(f'外键基线：{len(plan.foreign_key_baseline)} 条引用。')
        if plan.missing_files:
            print(
                f'缺失文件：{len(plan.missing_files)} 条'
                '（已纳入失效样本清理计划）'
            )
        if plan.conflicts:
            invalid_sample_ids = {
                entry.sample_id for entry in plan.invalid_samples
            }
            repaired_sample_ids = {
                entry.sample_id for entry in plan.readiness_updates
            }
            deleted_conflict_count = sum(
                entry.sample_id in invalid_sample_ids
                for entry in plan.conflicts
            )
            repaired_conflict_count = sum(
                entry.sample_id in repaired_sample_ids
                for entry in plan.conflicts
            )
            unresolved_conflict_count = (
                len(plan.conflicts)
                - deleted_conflict_count
                - repaired_conflict_count
            )
            conflict_detail = (
                f'计划删除 {deleted_conflict_count} 条，'
                f'复检修复 {repaired_conflict_count} 条'
            )
            if unresolved_conflict_count:
                conflict_detail += (
                    f'，待人工复核 {unresolved_conflict_count} 条'
                )
            print(
                f'指纹冲突：{len(plan.conflicts)} 条'
                f'（{conflict_detail}）'
            )
        if result:
            print(
                f'已合并 {result.merged_sample_count} 条，'
                f'按姓名裁减 {result.trimmed_sample_count} 条，'
                f'重连 {result.relinked_profile_count} 个音色，'
                f'删除 {result.detached_profile_count} 个同名上限关联音色，'
                f'本次同步删除 {result.removed_file_count} 个本地文件；'
                '其余资产由后台删除任务处理。'
            )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
