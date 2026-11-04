from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.schema_bootstrap import create_legacy_tables  # noqa: E402
from app.db.session import SessionLocal, configure_sqlite_engine, engine, ensure_schema  # noqa: E402
from app.models.canonical_import_batch import CanonicalImportBatch  # noqa: E402, F401
from app.models.canonical_passage import CanonicalPassage  # noqa: E402, F401
from app.models.canonical_passage_variant import CanonicalPassageVariant  # noqa: E402, F401
from app.services.canonical_knowledge_admin import (  # noqa: E402
    import_canonical_candidates,
    load_chinese_poetry_selection,
)


SOURCE_NAME = 'chinese-poetry/chinese-poetry'
SOURCE_URL = 'https://github.com/chinese-poetry/chinese-poetry'
SOURCE_LICENSE = 'MIT（代码仓库许可；原文版本仍需人工复核）'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path: Path, expected_sha256: str | None) -> str:
    if not path.is_file():
        raise SystemExit(f'文件不存在：{path}')
    actual_sha256 = _sha256(path)
    if expected_sha256 and actual_sha256 != expected_sha256.lower():
        raise SystemExit(
            f'{path.name} SHA-256 不匹配：期望 {expected_sha256}，实际 {actual_sha256}'
        )
    return actual_sha256


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='导入 chinese-poetry 精选候选语料')
    parser.add_argument('--tang', type=Path, required=True, help='唐诗三百首 JSON')
    parser.add_argument('--song-ci', type=Path, required=True, help='宋词三百首 JSON')
    parser.add_argument('--source-version', required=True, help='上游 Git commit SHA')
    parser.add_argument('--tang-sha256')
    parser.add_argument('--song-ci-sha256')
    parser.add_argument('--created-by', default='codex-import')
    parser.add_argument('--dry-run', action='store_true')
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    source_version = str(args.source_version).strip()
    if len(source_version) < 7:
        raise SystemExit('source-version 必须是可追溯的 Git commit SHA')

    tang_sha256 = _verify_file(args.tang, args.tang_sha256)
    song_ci_sha256 = _verify_file(args.song_ci, args.song_ci_sha256)
    tang_items = load_chinese_poetry_selection(
        args.tang,
        dynasty='唐',
        genre='唐诗',
        source_path='全唐诗/唐诗三百首.json',
        source_version=source_version,
    )
    song_ci_items = load_chinese_poetry_selection(
        args.song_ci,
        dynasty='宋',
        genre='宋词',
        source_path='宋词/宋词三百首.json',
        source_version=source_version,
    )

    configure_sqlite_engine(engine, enable_wal=True)
    create_legacy_tables(engine)
    ensure_schema()
    session = SessionLocal()
    try:
        reports = [
            import_canonical_candidates(
                session,
                tenant_id='default',
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                source_version=source_version,
                source_license=SOURCE_LICENSE,
                selection_label='唐诗三百首',
                items=tang_items,
                created_by=args.created_by,
                metadata={
                    'source_path': '全唐诗/唐诗三百首.json',
                    'source_sha256': tang_sha256,
                    'trust_policy': 'candidate_only_until_manual_review',
                },
            ),
            import_canonical_candidates(
                session,
                tenant_id='default',
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                source_version=source_version,
                source_license=SOURCE_LICENSE,
                selection_label='宋词三百首',
                items=song_ci_items,
                created_by=args.created_by,
                metadata={
                    'source_path': '宋词/宋词三百首.json',
                    'source_sha256': song_ci_sha256,
                    'trust_policy': 'candidate_only_until_manual_review',
                },
            ),
        ]
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
        print(json.dumps({
            'dry_run': bool(args.dry_run),
            'source_version': source_version,
            'batches': [
                {
                    'id': report.batch.id,
                    'selection': report.batch.selection_label,
                    'reused': report.reused,
                    'candidate_count': report.batch.candidate_count,
                    'passage_count': report.batch.passage_count,
                    'variant_count': report.batch.variant_count,
                    'duplicate_count': report.batch.duplicate_count,
                    'error_count': report.batch.error_count,
                    'status': report.batch.status,
                }
                for report in reports
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == '__main__':
    raise SystemExit(main())
