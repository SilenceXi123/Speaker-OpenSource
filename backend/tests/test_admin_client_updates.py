from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.schemas.client_update import ClientUpdateAdminRegisterRequest
from app.services.admin_auth import build_admin_session_token
from app.services.client_updates import (
    load_client_update_manifest,
    register_client_update_release,
)
from main import app


def _configure_admin_session(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", "configured-password-hash")
    monkeypatch.setattr(settings, "admin_session_secret", "session-secret")
    monkeypatch.setattr(settings, "admin_session_cookie_name", "speaker_admin_session")
    monkeypatch.setattr(settings, "admin_session_max_age_seconds", 3600)


def _authenticated_client(monkeypatch) -> TestClient:
    _configure_admin_session(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(settings.admin_session_cookie_name, build_admin_session_token("admin"))
    return client


def test_register_client_update_release_sets_latest(tmp_path: Path) -> None:
    package_file = tmp_path / "packages" / "stable-macos12" / "Speaker_0.2.2.app.tar.gz"
    package_file.parent.mkdir(parents=True)
    package_file.write_bytes(b"demo")

    register_client_update_release(
        tmp_path,
        ClientUpdateAdminRegisterRequest(
            channel="stable-macos12",
            version="0.2.2",
            target="darwin",
            arch="aarch64",
            package_file="packages/stable-macos12/Speaker_0.2.2.app.tar.gz",
            signature="signature-value",
            pub_date="2026-06-20T18:00:00+08:00",
            notes="demo",
        ),
    )

    manifest = load_client_update_manifest(tmp_path / "manifest.json")

    assert manifest.channels["stable-macos12"].latest == "0.2.2"
    assert "darwin-aarch64" in manifest.channels["stable-macos12"].versions["0.2.2"].platforms


def test_admin_client_update_releases_requires_admin_session(monkeypatch, tmp_path: Path) -> None:
    _configure_admin_session(monkeypatch)
    monkeypatch.setattr(settings, "client_update_dir", str(tmp_path))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/v1/admin/client-updates/releases")

    assert response.status_code == 401


def test_admin_client_update_releases_returns_release_summary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "client_update_dir", str(tmp_path))
    package_file = tmp_path / "packages" / "stable-macos12" / "Speaker_0.2.2.app.tar.gz"
    package_file.parent.mkdir(parents=True)
    package_file.write_bytes(b"demo")
    register_client_update_release(
        tmp_path,
        ClientUpdateAdminRegisterRequest(
            channel="stable-macos12",
            version="0.2.2",
            target="darwin",
            arch="aarch64",
            package_file="packages/stable-macos12/Speaker_0.2.2.app.tar.gz",
            signature="signature-value",
            pub_date="2026-06-20T18:00:00+08:00",
            notes="demo",
        ),
    )
    client = _authenticated_client(monkeypatch)

    response = client.get("/api/v1/admin/client-updates/releases")

    assert response.status_code == 200
    assert response.json()[0]["channel"] == "stable-macos12"
    assert response.json()[0]["latest"] == "0.2.2"


def test_admin_client_update_releases_maps_malformed_manifest_to_400(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "client_update_dir", str(tmp_path))
    (tmp_path / "manifest.json").write_text("{invalid", encoding="utf-8")
    client = _authenticated_client(monkeypatch)

    response = client.get("/api/v1/admin/client-updates/releases")

    assert response.status_code == 400
    assert "无法读取客户端更新 manifest" in response.json()["detail"]


def test_admin_upload_client_update_release_saves_files_and_registers_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "client_update_dir", str(tmp_path))
    client = _authenticated_client(monkeypatch)

    response = client.post(
        "/api/v1/admin/client-updates/releases/upload",
        data={
            "channel": "stable-macos12",
            "version": "0.2.3",
            "target": "darwin",
            "arch": "universal",
            "notes": "上传即登记",
        },
        files={
            "package": ("Speaker 客户端.app.tar.gz", b"package-bytes", "application/gzip"),
            "signature_file": (
                "Speaker 客户端.app.tar.gz.sig",
                b"signature-value\n",
                "text/plain",
            ),
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["channel"] == "stable-macos12"
    assert response.json()[0]["latest"] == "0.2.3"
    assert (tmp_path / "packages" / "stable-macos12" / "Speaker 客户端.app.tar.gz").read_bytes() == b"package-bytes"
    assert (tmp_path / "packages" / "stable-macos12" / "Speaker 客户端.app.tar.gz.sig").read_text(
        encoding="utf-8"
    ) == "signature-value\n"

    manifest = load_client_update_manifest(tmp_path / "manifest.json")
    platform_entry = manifest.channels["stable-macos12"].versions["0.2.3"].platforms["darwin-universal"]
    assert platform_entry.file == "packages/stable-macos12/Speaker 客户端.app.tar.gz"
    assert platform_entry.signature == "signature-value"
    assert platform_entry.sha256 is not None
    assert platform_entry.size_bytes == len(b"package-bytes")
