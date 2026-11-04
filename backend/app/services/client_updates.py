from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from pydantic import ValidationError

from app.schemas.client_update import (
    ClientUpdateAdminRegisterRequest,
    ClientUpdateChannelEntry,
    ClientUpdateManifest,
    ClientUpdatePlatformEntry,
    ClientUpdateReleaseSummary,
    ClientUpdateVersionEntry,
    TauriUpdatePlatformResponse,
    TauriUpdateResponse,
)


MANIFEST_FILENAME = 'manifest.json'


class ClientUpdateManifestError(ValueError):
    pass


def _manifest_path(update_dir: Path) -> Path:
    return update_dir / MANIFEST_FILENAME


def _parse_semver(value: str) -> tuple[int, int, int, str]:
    match = re.match(r'^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$', value.strip())
    if not match:
        return (0, 0, 0, value)
    major, minor, patch, suffix = match.groups()
    return (int(major), int(minor or 0), int(patch or 0), suffix or '')


def is_version_newer(candidate: str, current: str) -> bool:
    candidate_parts = _parse_semver(candidate)
    current_parts = _parse_semver(current)
    return candidate_parts > current_parts


def build_platform_key(target: str, arch: str) -> str:
    normalized_target = target.strip()
    normalized_arch = arch.strip()
    if not normalized_arch or normalized_arch in normalized_target:
        return normalized_target
    return f'{normalized_target}-{normalized_arch}'


def build_platform_key_candidates(target: str, arch: str) -> list[str]:
    platform_key = build_platform_key(target, arch)
    normalized_target = target.strip()
    normalized_arch = arch.strip()
    candidates = [platform_key]
    if normalized_target and normalized_target != platform_key:
        candidates.append(normalized_target)

    base_target = normalized_target
    if normalized_arch and base_target.endswith(f'-{normalized_arch}'):
        base_target = base_target[: -(len(normalized_arch) + 1)]
    if base_target:
        candidates.append(f'{base_target}-universal')

    unique_candidates: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def load_client_update_manifest(path: Path) -> ClientUpdateManifest:
    if not path.exists():
        return ClientUpdateManifest()

    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return ClientUpdateManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ClientUpdateManifestError(f'无法读取客户端更新 manifest: {exc}') from exc


def save_client_update_manifest(path: Path, manifest: ClientUpdateManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def resolve_client_update_package_path(update_dir: Path, package_file: str) -> Path:
    relative_path = Path(package_file)
    if relative_path.is_absolute() or '..' in relative_path.parts:
        raise ClientUpdateManifestError('客户端更新包路径不允许使用绝对路径或上级目录')

    update_root = update_dir.resolve()
    package_path = (update_root / relative_path).resolve()
    if not package_path.is_relative_to(update_root):
        raise ClientUpdateManifestError('客户端更新包路径必须位于更新目录内')
    return package_path


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _package_url(public_base_url: str, package_file: str) -> str:
    parts = Path(package_file).parts
    if parts and parts[0] == 'packages':
        parts = parts[1:]
    encoded_path = '/'.join(quote(part) for part in parts)
    return f'{public_base_url.rstrip("/")}/packages/{encoded_path}'


def build_tauri_update_response(
    manifest: ClientUpdateManifest,
    channel: str,
    target: str,
    arch: str,
    current_version: str,
    public_base_url: str,
) -> TauriUpdateResponse | None:
    channel_entry = manifest.channels.get(channel)
    if not channel_entry or not channel_entry.latest:
        return None

    version_entry = channel_entry.versions.get(channel_entry.latest)
    if not version_entry or not is_version_newer(version_entry.version, current_version):
        return None

    response_platform_key = build_platform_key(target, arch)
    platform_entry = None
    for candidate_key in build_platform_key_candidates(target, arch):
        platform_entry = version_entry.platforms.get(candidate_key)
        if platform_entry:
            break
    if not platform_entry:
        return None

    return TauriUpdateResponse(
        version=version_entry.version,
        notes=version_entry.notes,
        pub_date=version_entry.pub_date,
        platforms={
            response_platform_key: TauriUpdatePlatformResponse(
                url=_package_url(public_base_url, platform_entry.file),
                signature=platform_entry.signature,
            ),
        },
    )


def list_client_update_releases(update_dir: Path) -> list[ClientUpdateReleaseSummary]:
    manifest_path = _manifest_path(update_dir)
    manifest = load_client_update_manifest(manifest_path)
    updated_at = datetime.fromtimestamp(manifest_path.stat().st_mtime) if manifest_path.exists() else None
    return [
        ClientUpdateReleaseSummary(
            channel=channel,
            latest=entry.latest,
            version_count=len(entry.versions),
            updated_at=updated_at,
        )
        for channel, entry in sorted(manifest.channels.items())
    ]


def register_client_update_release(
    update_dir: Path,
    payload: ClientUpdateAdminRegisterRequest,
) -> ClientUpdateManifest:
    package_path = resolve_client_update_package_path(update_dir, payload.package_file)
    if not package_path.exists() or not package_path.is_file():
        raise ClientUpdateManifestError(f'客户端更新包不存在: {payload.package_file}')

    manifest_path = _manifest_path(update_dir)
    manifest = load_client_update_manifest(manifest_path)
    channel = manifest.channels.setdefault(payload.channel, ClientUpdateChannelEntry())
    version_entry = channel.versions.get(payload.version)
    if not version_entry:
        version_entry = ClientUpdateVersionEntry(
            version=payload.version,
            pub_date=payload.pub_date,
            notes=payload.notes,
            mandatory=payload.mandatory,
            minimum_supported_version=payload.minimum_supported_version,
        )

    platform_key = build_platform_key(payload.target, payload.arch)
    version_entry.pub_date = payload.pub_date
    version_entry.notes = payload.notes
    version_entry.mandatory = payload.mandatory
    version_entry.minimum_supported_version = payload.minimum_supported_version
    version_entry.platforms[platform_key] = ClientUpdatePlatformEntry(
        file=payload.package_file,
        signature=payload.signature,
        sha256=payload.sha256 or calculate_sha256(package_path),
        size_bytes=payload.size_bytes if payload.size_bytes is not None else package_path.stat().st_size,
    )
    channel.versions[payload.version] = version_entry
    if payload.mark_latest:
        channel.latest = payload.version

    save_client_update_manifest(manifest_path, manifest)
    return manifest
