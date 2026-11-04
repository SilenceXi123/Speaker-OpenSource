import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.schemas.client_update import ClientUpdateAdminRegisterRequest
from app.services.client_updates import (
    ClientUpdateManifestError,
    build_platform_key,
    build_platform_key_candidates,
    build_tauri_update_response,
    load_client_update_manifest,
    register_client_update_release,
    resolve_client_update_package_path,
)
from main import app


client = TestClient(app)


def _write_manifest(path: Path) -> None:
    path.write_text(
        """
        {
          "channels": {
            "stable-macos12": {
              "latest": "0.2.2",
              "versions": {
                "0.2.2": {
                  "version": "0.2.2",
                  "pub_date": "2026-06-20T18:00:00+08:00",
                  "notes": "demo",
                  "mandatory": false,
                  "minimum_supported_version": "0.2.1",
                  "platforms": {
                    "darwin-aarch64": {
                      "file": "packages/stable-macos12/Speaker_0.2.2.app.tar.gz",
                      "signature": "signature-value"
                    }
                  }
                }
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )


def test_load_manifest_returns_empty_channels_when_file_missing(tmp_path: Path) -> None:
    manifest = load_client_update_manifest(tmp_path / "missing.json")

    assert manifest.channels == {}


def test_load_manifest_raises_error_for_invalid_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ClientUpdateManifestError):
        load_client_update_manifest(manifest_path)


def test_build_platform_key_combines_target_and_arch() -> None:
    assert build_platform_key("darwin", "aarch64") == "darwin-aarch64"


def test_build_platform_key_candidates_include_universal_fallback() -> None:
    assert build_platform_key_candidates("darwin", "aarch64") == [
        "darwin-aarch64",
        "darwin",
        "darwin-universal",
    ]
    assert build_platform_key_candidates("darwin-aarch64", "aarch64") == [
        "darwin-aarch64",
        "darwin-universal",
    ]


def test_build_tauri_update_response_returns_none_when_current_is_latest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    manifest = load_client_update_manifest(manifest_path)

    response = build_tauri_update_response(
        manifest,
        channel="stable-macos12",
        target="darwin",
        arch="aarch64",
        current_version="0.2.2",
        public_base_url="http://127.0.0.1:8787/api/v1/client-updates",
    )

    assert response is None


def test_build_tauri_update_response_returns_platform_payload(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    manifest = load_client_update_manifest(manifest_path)

    response = build_tauri_update_response(
        manifest,
        channel="stable-macos12",
        target="darwin",
        arch="aarch64",
        current_version="0.2.1",
        public_base_url="http://127.0.0.1:8787/api/v1/client-updates",
    )

    assert response is not None
    assert response.version == "0.2.2"
    assert response.notes == "demo"
    assert response.platforms["darwin-aarch64"].signature == "signature-value"
    assert response.platforms["darwin-aarch64"].url.endswith(
        "/packages/stable-macos12/Speaker_0.2.2.app.tar.gz"
    )


def test_build_tauri_update_response_quotes_url_segments(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "channels": {
            "stable-macos12": {
              "latest": "0.2.3",
              "versions": {
                "0.2.3": {
                  "version": "0.2.3",
                  "pub_date": "2026-06-20T18:00:00+08:00",
                  "notes": "demo",
                  "platforms": {
                    "darwin-aarch64": {
                      "file": "packages/stable macos12/Speaker 中文 0.2.3.app.tar.gz",
                      "signature": "signature-value"
                    }
                  }
                }
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )
    manifest = load_client_update_manifest(manifest_path)

    response = build_tauri_update_response(
        manifest,
        channel="stable-macos12",
        target="darwin",
        arch="aarch64",
        current_version="0.2.2",
        public_base_url="http://127.0.0.1:8787/api/v1/client-updates/",
    )

    assert response is not None
    assert response.platforms["darwin-aarch64"].url.endswith(
        "/packages/stable%20macos12/Speaker%20%E4%B8%AD%E6%96%87%200.2.3.app.tar.gz"
    )


def test_build_tauri_update_response_uses_universal_package_for_arch_request(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "channels": {
            "stable-macos12": {
              "latest": "0.2.3",
              "versions": {
                "0.2.3": {
                  "version": "0.2.3",
                  "pub_date": "2026-06-20T18:00:00+08:00",
                  "notes": "universal",
                  "platforms": {
                    "darwin-universal": {
                      "file": "packages/stable-macos12/Speaker_0.2.3.app.tar.gz",
                      "signature": "universal-signature"
                    }
                  }
                }
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )
    manifest = load_client_update_manifest(manifest_path)

    response = build_tauri_update_response(
        manifest,
        channel="stable-macos12",
        target="darwin",
        arch="aarch64",
        current_version="0.2.2",
        public_base_url="http://127.0.0.1:8787/api/v1/client-updates",
    )

    assert response is not None
    assert response.platforms["darwin-aarch64"].signature == "universal-signature"
    assert response.platforms["darwin-aarch64"].url.endswith(
        "/packages/stable-macos12/Speaker_0.2.3.app.tar.gz"
    )


def test_resolve_package_path_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(ClientUpdateManifestError):
        resolve_client_update_package_path(tmp_path, "/tmp/secret.txt")


def test_resolve_package_path_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ClientUpdateManifestError):
        resolve_client_update_package_path(tmp_path, "../secret.txt")


def test_resolve_package_path_allows_safe_nested_path(tmp_path: Path) -> None:
    resolved = resolve_client_update_package_path(
        tmp_path,
        "packages/stable-macos12/Speaker_0.2.2.app.tar.gz",
    )

    assert resolved == tmp_path / "packages/stable-macos12/Speaker_0.2.2.app.tar.gz"


def test_register_client_update_release_auto_fills_package_metadata_and_latest(tmp_path: Path) -> None:
    package_path = tmp_path / "packages" / "stable-macos12" / "Speaker_0.2.2.app.tar.gz"
    package_path.parent.mkdir(parents=True)
    payload = b"demo package"
    package_path.write_bytes(payload)

    manifest = register_client_update_release(
        tmp_path,
        ClientUpdateAdminRegisterRequest(
            channel="stable-macos12",
            version="0.2.2",
            package_file="packages/stable-macos12/Speaker_0.2.2.app.tar.gz",
            signature="signature-value",
            pub_date="2026-06-20T18:00:00+08:00",
            notes="demo",
        ),
    )

    channel = manifest.channels["stable-macos12"]
    platform = channel.versions["0.2.2"].platforms["darwin-universal"]
    assert channel.latest == "0.2.2"
    assert platform.sha256 == hashlib.sha256(payload).hexdigest()
    assert platform.size_bytes == len(payload)


def test_latest_client_update_returns_204_when_manifest_missing(tmp_path: Path) -> None:
    original_dir = settings.client_update_dir
    original_enabled = settings.client_update_enabled
    try:
        settings.client_update_dir = str(tmp_path)
        settings.client_update_enabled = True

        response = client.get(
            "/api/v1/client-updates/latest/darwin/aarch64/0.2.1",
            params={"channel": "stable-macos12"},
        )

        assert response.status_code == 204
        assert response.content == b""
    finally:
        settings.client_update_dir = original_dir
        settings.client_update_enabled = original_enabled


def test_latest_client_update_returns_204_when_disabled(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    original_dir = settings.client_update_dir
    original_enabled = settings.client_update_enabled
    try:
        settings.client_update_dir = str(tmp_path)
        settings.client_update_enabled = False

        response = client.get("/api/v1/client-updates/latest/darwin/aarch64/0.2.1")

        assert response.status_code == 204
        assert response.content == b""
    finally:
        settings.client_update_dir = original_dir
        settings.client_update_enabled = original_enabled


def test_latest_client_update_returns_payload_when_version_is_newer(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    package_path = tmp_path / "packages" / "stable-macos12" / "Speaker_0.2.2.app.tar.gz"
    package_path.parent.mkdir(parents=True)
    package_path.write_bytes(b"demo package")
    original_dir = settings.client_update_dir
    original_enabled = settings.client_update_enabled
    original_public_base_url = settings.client_update_public_base_url
    try:
        settings.client_update_dir = str(tmp_path)
        settings.client_update_enabled = True
        settings.client_update_public_base_url = "http://updates.local/api/v1/client-updates/"

        response = client.get(
            "/api/v1/client-updates/latest/darwin/aarch64/0.2.1",
            params={"channel": "stable-macos12"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["version"] == "0.2.2"
        assert payload["notes"] == "demo"
        assert payload["platforms"]["darwin-aarch64"]["signature"] == "signature-value"
        assert payload["platforms"]["darwin-aarch64"]["url"] == (
            "http://updates.local/api/v1/client-updates/packages/stable-macos12/"
            "Speaker_0.2.2.app.tar.gz"
        )

        package_response = client.get(urlsplit(payload["platforms"]["darwin-aarch64"]["url"]).path)
        assert package_response.status_code == 200
        assert package_response.content == b"demo package"
    finally:
        settings.client_update_dir = original_dir
        settings.client_update_enabled = original_enabled
        settings.client_update_public_base_url = original_public_base_url


def test_download_client_update_package_returns_file(tmp_path: Path) -> None:
    package_path = tmp_path / "packages" / "stable-macos12" / "Speaker_0.2.2.app.tar.gz"
    package_path.parent.mkdir(parents=True)
    package_path.write_bytes(b"demo package")
    original_dir = settings.client_update_dir
    try:
        settings.client_update_dir = str(tmp_path)

        response = client.get(
            "/api/v1/client-updates/packages/stable-macos12/Speaker_0.2.2.app.tar.gz"
        )

        assert response.status_code == 200
        assert response.content == b"demo package"
        assert response.headers["content-type"] == "application/octet-stream"
    finally:
        settings.client_update_dir = original_dir


def test_download_client_update_package_rejects_path_traversal(tmp_path: Path) -> None:
    original_dir = settings.client_update_dir
    try:
        settings.client_update_dir = str(tmp_path)

        response = client.get("/api/v1/client-updates/packages/%2E%2E/secret.txt")

        assert response.status_code == 400
    finally:
        settings.client_update_dir = original_dir


def test_download_client_update_package_returns_404_for_missing_package(tmp_path: Path) -> None:
    original_dir = settings.client_update_dir
    try:
        settings.client_update_dir = str(tmp_path)

        response = client.get("/api/v1/client-updates/packages/missing.tar.gz")

        assert response.status_code == 404
    finally:
        settings.client_update_dir = original_dir


def test_post_client_update_event_returns_204() -> None:
    response = client.post(
        "/api/v1/client-updates/events",
        json={
            "event_type": "install_started",
            "current_version": "0.2.1",
            "next_version": "0.2.2",
            "channel": "stable-macos12",
            "message": "demo",
        },
    )

    assert response.status_code == 204
    assert response.content == b""
