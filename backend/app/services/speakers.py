from __future__ import annotations

from typing import Optional

from app.schemas.speaker import SpeakerOption, VoiceprintCandidate


def build_speaker_options(
    normalized_segments: list[dict],
    mappings: Optional[dict[str, str]] = None,
    voiceprint_matches: Optional[dict[str, dict]] = None,
) -> list[SpeakerOption]:
    mappings = mappings or {}
    voiceprint_matches = voiceprint_matches or {}
    grouped: dict[str, int] = {}
    for segment in normalized_segments:
        speaker_key = str(segment.get('speakerKey') or 'speaker_0')
        grouped[speaker_key] = grouped.get(speaker_key, 0) + 1

    return [
        SpeakerOption(
            speaker_key=speaker_key,
            speaker_name=mappings.get(speaker_key),
            segment_count=count,
            voiceprint_status=(voiceprint_matches.get(speaker_key) or {}).get('status'),
            voiceprint_person_name=_resolve_voiceprint_person_name(voiceprint_matches.get(speaker_key)),
            voiceprint_score=_resolve_voiceprint_score(voiceprint_matches.get(speaker_key)),
            voiceprint_candidates=_build_voiceprint_candidates(voiceprint_matches.get(speaker_key)),
        )
        for speaker_key, count in sorted(grouped.items())
    ]


def _resolve_voiceprint_person_name(match_payload: Optional[dict]) -> Optional[str]:
    if not match_payload:
        return None
    candidates = match_payload.get('candidates') or []
    if candidates:
        return str(candidates[0].get('person_name') or '') or None
    return None


def _resolve_voiceprint_score(match_payload: Optional[dict]) -> Optional[float]:
    if not match_payload:
        return None
    score = match_payload.get('score')
    return float(score) if score is not None else None


def _build_voiceprint_candidates(match_payload: Optional[dict]) -> list[VoiceprintCandidate]:
    if not match_payload:
        return []
    return [
        VoiceprintCandidate(
            person_id=str(item.get('person_id') or ''),
            person_name=str(item.get('person_name') or ''),
            score=float(item.get('score') or 0.0),
        )
        for item in (match_payload.get('candidates') or [])
        if item.get('person_id') and item.get('person_name') is not None
    ]
