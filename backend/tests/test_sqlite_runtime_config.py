from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine

from app.db.session import _set_sqlite_wal, configure_sqlite_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _pragmas(connection) -> tuple[int, int, str]:
    foreign_keys = connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one()
    busy_timeout = connection.exec_driver_sql('PRAGMA busy_timeout').scalar_one()
    journal_mode = connection.exec_driver_sql('PRAGMA journal_mode').scalar_one()
    return foreign_keys, busy_timeout, journal_mode


def test_file_sqlite_config_applies_to_two_connections_and_reconnect(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    configure_sqlite_engine(engine)

    with engine.connect() as first, engine.connect() as second:
        assert first.connection.driver_connection is not second.connection.driver_connection
        assert _pragmas(first) == (1, 5000, 'wal')
        assert _pragmas(second) == (1, 5000, 'wal')

    engine.dispose()

    with engine.connect() as reconnected:
        assert _pragmas(reconnected) == (1, 5000, 'wal')


def test_configure_sqlite_engine_is_idempotent_and_instance_scoped(
    tmp_path: Path,
) -> None:
    configured = create_engine(f"sqlite:///{tmp_path / 'configured.db'}")
    base_only = create_engine(f"sqlite:///{tmp_path / 'base-only.db'}")
    unconfigured = create_engine(f"sqlite:///{tmp_path / 'unconfigured.db'}")

    configure_sqlite_engine(configured, enable_wal=False)
    listener_count = len(configured.pool.dispatch.connect)
    configure_sqlite_engine(configured, enable_wal=False)

    assert len(configured.pool.dispatch.connect) == listener_count
    configure_sqlite_engine(configured, enable_wal=True)
    wal_listener_count = len(configured.pool.dispatch.connect)
    configure_sqlite_engine(configured, enable_wal=True)
    assert wal_listener_count == listener_count + 1
    assert len(configured.pool.dispatch.connect) == wal_listener_count
    with configured.connect() as configured_connection:
        assert _pragmas(configured_connection) == (1, 5000, 'wal')

    configure_sqlite_engine(base_only, enable_wal=False)
    configure_sqlite_engine(base_only, enable_wal=False)
    with base_only.connect() as base_connection:
        assert _pragmas(base_connection) == (1, 5000, 'delete')

    with unconfigured.connect() as unconfigured_connection:
        foreign_keys, _, journal_mode = _pragmas(unconfigured_connection)
        assert foreign_keys == 0
        assert journal_mode != 'wal'


def test_memory_sqlite_does_not_request_wal() -> None:
    statements: list[str] = []

    def create_connection():
        connection = sqlite3.connect(':memory:')
        connection.set_trace_callback(statements.append)
        return connection

    engine = create_engine('sqlite://', creator=create_connection)
    configure_sqlite_engine(engine)

    with engine.connect() as connection:
        foreign_keys, busy_timeout, journal_mode = _pragmas(connection)

    assert (foreign_keys, busy_timeout, journal_mode) == (1, 5000, 'memory')
    assert not any('journal_mode=wal' in statement.lower() for statement in statements)


@pytest.mark.parametrize(
    ('sqlalchemy_url', 'sqlite_uri'),
    [
        (
            'sqlite:///file::memory:?cache=shared&uri=true',
            'file::memory:?cache=shared',
        ),
        (
            'sqlite:///file:provider-cost?mode=memory&cache=shared&uri=true',
            'file:provider-cost?mode=memory&cache=shared',
        ),
    ],
)
def test_shared_memory_sqlite_uri_does_not_request_wal(
    sqlalchemy_url: str,
    sqlite_uri: str,
) -> None:
    statements: list[str] = []

    def create_connection():
        connection = sqlite3.connect(sqlite_uri, uri=True)
        connection.set_trace_callback(statements.append)
        return connection

    engine = create_engine(sqlalchemy_url, creator=create_connection)
    configure_sqlite_engine(engine)

    with engine.connect() as connection:
        assert _pragmas(connection) == (1, 5000, 'memory')

    assert not any('journal_mode=wal' in statement.lower() for statement in statements)


@pytest.mark.parametrize(
    'sqlalchemy_url',
    [
        'sqlite:///file::memory:',
        'sqlite:///file::memory:archive.db?uri=true',
    ],
)
def test_memory_like_file_urls_enable_wal(
    sqlalchemy_url: str,
    tmp_path: Path,
) -> None:
    statements: list[str] = []

    def create_connection():
        connection = sqlite3.connect(tmp_path / 'memory-like-file.db')
        connection.set_trace_callback(statements.append)
        return connection

    engine = create_engine(sqlalchemy_url, creator=create_connection)
    configure_sqlite_engine(engine)

    with engine.connect() as connection:
        assert _pragmas(connection) == (1, 5000, 'wal')

    assert any('journal_mode=wal' in statement.lower() for statement in statements)


def test_existing_project_migration_dry_run_does_not_enable_wal(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / 'legacy-projects.db'
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': str(BACKEND_DIR),
        'PYTHONPYCACHEPREFIX': str(tmp_path / 'pycache'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'SQLITE_PATH': str(database_path),
    })

    result = subprocess.run(
        [sys.executable, '-m', 'scripts.migrate_v2_projects'],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout)['mode'] == 'dry-run'
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'


class _RejectedWalCursor:
    def __init__(self) -> None:
        self.closed = False
        self._row = None
        self._rows = []

    def execute(self, statement: str) -> None:
        if statement == 'PRAGMA journal_mode=WAL':
            self._row = ('delete',)
        elif statement == 'PRAGMA database_list':
            self._rows = [(0, 'main', '/tmp/provider-cost-refused.db')]

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows

    def close(self) -> None:
        self.closed = True


class _IsolationConnection:
    def __init__(self) -> None:
        self.isolation_level = 'DEFERRED'
        self.test_cursor = _RejectedWalCursor()

    def cursor(self) -> _RejectedWalCursor:
        return self.test_cursor


class _AutocommitConnection:
    def __init__(self) -> None:
        self.autocommit = False
        self.test_cursor = _RejectedWalCursor()

    def cursor(self) -> _RejectedWalCursor:
        return self.test_cursor


@pytest.mark.parametrize(
    ('connection_factory', 'attribute', 'expected_value'),
    [
        (_IsolationConnection, 'isolation_level', 'DEFERRED'),
        (_AutocommitConnection, 'autocommit', False),
    ],
)
def test_sqlite_wal_rejection_restores_connection_state(
    connection_factory,
    attribute: str,
    expected_value,
) -> None:
    connection = connection_factory()

    with pytest.raises(
        RuntimeError,
        match=r'/tmp/provider-cost-refused\.db.*delete',
    ):
        _set_sqlite_wal(connection)

    assert connection.test_cursor.closed is True
    assert getattr(connection, attribute) == expected_value


def test_global_engine_runtime_pragmas_and_cross_thread_connection(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / 'global-runtime.db'
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': str(BACKEND_DIR),
        'PYTHONPYCACHEPREFIX': str(tmp_path / 'pycache'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'SQLITE_PATH': str(database_path),
    })
    script = """
import json
import queue
import threading

import main
from app.db.session import engine

_ = main.app

with engine.connect() as connection:
    raw_connection = connection.connection.driver_connection
    pragmas = {
        'foreign_keys': raw_connection.execute('PRAGMA foreign_keys').fetchone()[0],
        'busy_timeout': raw_connection.execute('PRAGMA busy_timeout').fetchone()[0],
        'journal_mode': raw_connection.execute('PRAGMA journal_mode').fetchone()[0],
    }
    errors = queue.Queue()
    results = queue.Queue()

    def run_query():
        try:
            results.put(raw_connection.execute('SELECT 42').fetchone()[0])
        except BaseException as exc:
            errors.put(repr(exc))

    thread = threading.Thread(target=run_query)
    thread.start()
    thread.join(timeout=5)
    if thread.is_alive():
        raise RuntimeError('cross-thread SQLite query timed out')
    if not errors.empty():
        raise RuntimeError(errors.get())
    print(json.dumps({'pragmas': pragmas, 'thread_result': results.get_nowait()}))
"""

    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        'pragmas': {
            'foreign_keys': 1,
            'busy_timeout': 5000,
            'journal_mode': 'wal',
        },
        'thread_result': 42,
    }
