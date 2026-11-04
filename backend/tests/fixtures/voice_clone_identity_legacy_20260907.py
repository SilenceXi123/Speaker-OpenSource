from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import unicodedata


_WHITESPACE_PATTERN = re.compile(r'\s+')


@dataclass(frozen=True)
class VoiceCloneAudioIdentity:
    content_sha256: str
    file_size_bytes: int


def hash_voice_clone_audio(path: Path) -> VoiceCloneAudioIdentity:
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
    return VoiceCloneAudioIdentity(
        content_sha256=digest.hexdigest(),
        file_size_bytes=size,
    )


def normalize_voice_clone_name(value: str) -> str:
    normalized = unicodedata.normalize('NFKC', str(value or ''))
    return _WHITESPACE_PATTERN.sub(' ', normalized).strip().casefold()
