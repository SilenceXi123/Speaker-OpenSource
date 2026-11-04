from pathlib import Path

from app.core.config import PROJECT_ROOT, settings


def test_settings_paths_resolve_from_project_root() -> None:
    expected_root = Path(__file__).resolve().parents[2]

    assert PROJECT_ROOT == expected_root
    assert settings.sqlite_url == f'sqlite:///{expected_root / "backend/data/speaker.db"}'
    assert settings.upload_dir_path == expected_root / 'backend/data/tmp/uploads'
    assert settings.audio_dir_path == expected_root / 'backend/data/tmp/audio'
    assert settings.render_dir_path == expected_root / 'backend/data/tmp/render'
