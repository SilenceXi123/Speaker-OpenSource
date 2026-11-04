from __future__ import annotations

import argparse
import json

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.session import SessionLocal, engine
from app.models.job import Job  # noqa: F401
from app.models.person import Person  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.speaker_memory import SpeakerMemory  # noqa: F401
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.services.projects import migrate_legacy_projects, preview_legacy_project_migration


def main() -> None:
    parser = argparse.ArgumentParser(description='Preview or apply Speaker 2.0 project migration.')
    parser.add_argument('--apply', action='store_true', help='Create projects and backfill jobs.project_id.')
    args = parser.parse_args()

    if not args.apply:
        report = preview_legacy_project_migration(engine)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    _ensure_project_migration_schema(engine)
    session = SessionLocal()
    try:
        report = migrate_legacy_projects(session, apply=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        session.close()


def _ensure_project_migration_schema(target_engine: Engine) -> None:
    Project.__table__.create(bind=target_engine, checkfirst=True)
    inspector = inspect(target_engine)
    if 'jobs' not in inspector.get_table_names():
        raise RuntimeError('jobs 表不存在，不能执行项目迁移')
    job_columns = {column['name'] for column in inspector.get_columns('jobs')}
    with target_engine.begin() as connection:
        if 'project_id' not in job_columns:
            connection.execute(text('ALTER TABLE jobs ADD COLUMN project_id TEXT'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_jobs_project_id ON jobs(project_id)'))


if __name__ == '__main__':
    main()
