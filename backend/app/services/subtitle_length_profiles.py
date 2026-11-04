from __future__ import annotations

from typing import Any

from app.services.subtitles import build_subtitle_length_profile


def job_subtitle_length_profile(job: Any) -> dict[str, int | str]:
    metrics = getattr(job, 'processing_metrics', None)
    profile = metrics.get('subtitle_length_profile') if isinstance(metrics, dict) else None
    if not isinstance(profile, dict):
        return build_subtitle_length_profile()
    return build_subtitle_length_profile(profile.get('mode'), profile.get('max_chars'))
