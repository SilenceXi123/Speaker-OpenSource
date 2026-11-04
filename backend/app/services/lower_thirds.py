from __future__ import annotations

from pathlib import Path
from typing import Optional


def build_lower_third_asset(
    file_name: str,
    object_key: str,
    public_url: Optional[str] = None,
    local_path: Optional[str] = None,
    export_path: Optional[str] = None,
) -> dict:
    speaker_name = Path(file_name).stem.strip()
    asset = {
        'speaker_name': speaker_name,
        'file_name': Path(file_name).name,
        'object_key': object_key,
    }
    if public_url:
        asset['public_url'] = public_url
    if local_path:
        asset['local_path'] = local_path
    if export_path:
        asset['export_path'] = export_path
    return asset


def match_lower_thirds(speaker_names: list[str], assets: list[dict]) -> tuple[dict[str, dict], list[str]]:
    asset_map = {_normalize_name(asset.get('speaker_name', '')): asset for asset in assets}
    matched: dict[str, dict] = {}
    missing: list[str] = []

    for speaker_name in speaker_names:
        normalized = _normalize_name(speaker_name)
        asset = asset_map.get(normalized)
        if asset:
            matched[speaker_name] = asset
        else:
            missing.append(speaker_name)
    return matched, missing


def _normalize_name(value: str) -> str:
    return ''.join(ch.lower() for ch in value.strip() if ch.isalnum())
