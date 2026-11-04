from __future__ import annotations

from collections.abc import Sequence

from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    RawAsrToken,
    SemanticBoundaryReason,
    SemanticUtterance,
    SemanticUtteranceArtifact,
    SemanticUtteranceBoundary,
    SemanticUtteranceProfile,
    canonical_sha256,
)


SEMANTIC_UTTERANCE_SCHEMA_VERSION = 'semantic-utterances.v1'
DEFAULT_SEMANTIC_UTTERANCE_PROFILE = SemanticUtteranceProfile(
    profile_version='semantic-utterance.v1',
    max_continuation_gap_ms=1200,
    terminal_pause_ms=320,
    max_display_chars=120,
)
_TERMINAL_PUNCTUATION = ('。', '！', '？', '!', '?', '；', ';')


def build_semantic_utterances_v2(
    snapshot: RawAsrSnapshot,
    *,
    profile: SemanticUtteranceProfile = DEFAULT_SEMANTIC_UTTERANCE_PROFILE,
) -> SemanticUtteranceArtifact:
    groups: list[tuple[list[RawAsrToken], SemanticUtteranceBoundary]] = []
    current_tokens = list(snapshot.tokens[:1])
    current_display_chars = (
        _token_display_char_count(current_tokens[0]) if current_tokens else 0
    )
    current_boundary = (
        SemanticUtteranceBoundary(
            reason='tape_start',
            right_token_id=current_tokens[0].token_id,
        )
        if current_tokens
        else None
    )

    for token in snapshot.tokens[1:]:
        previous = current_tokens[-1]
        token_display_chars = _token_display_char_count(token)
        reason = _boundary_reason(
            previous,
            token,
            current_display_chars,
            token_display_chars,
            profile,
        )
        if reason is None:
            current_tokens.append(token)
            current_display_chars += token_display_chars
            continue

        assert current_boundary is not None
        groups.append((current_tokens, current_boundary))
        current_tokens = [token]
        current_display_chars = token_display_chars
        current_boundary = SemanticUtteranceBoundary(
            reason=reason,
            left_token_id=previous.token_id,
            right_token_id=token.token_id,
            gap_ms=_gap_ms(previous, token),
        )

    if current_tokens:
        assert current_boundary is not None
        groups.append((current_tokens, current_boundary))

    utterances = tuple(
        _build_utterance(snapshot.snapshot_hash, profile, tokens, boundary)
        for tokens, boundary in groups
    )
    content = {
        'schema_version': SEMANTIC_UTTERANCE_SCHEMA_VERSION,
        'raw_snapshot_hash': snapshot.snapshot_hash,
        'profile': profile.model_dump(mode='json'),
        'utterances': [item.model_dump(mode='json') for item in utterances],
    }
    return SemanticUtteranceArtifact(
        **content,
        artifact_hash=canonical_sha256(content),
    )


def _boundary_reason(
    previous: RawAsrToken,
    current: RawAsrToken,
    current_display_chars: int,
    token_display_chars: int,
    profile: SemanticUtteranceProfile,
) -> SemanticBoundaryReason | None:
    previous_speaker = _speaker_identity(previous)
    current_speaker = _speaker_identity(current)
    if previous_speaker != current_speaker:
        if previous_speaker is None or current_speaker is None:
            return 'speaker_evidence_transition'
        return 'speaker_change'

    same_segment = previous.segment_id == current.segment_id
    if not same_segment and (
        previous.timing_precision == 'missing'
        or current.timing_precision == 'missing'
    ):
        return 'missing_time_segment_boundary'

    gap_ms = _gap_ms(previous, current)
    has_incomparable_internal_timing = (
        same_segment
        and (
            previous.timing_precision != 'word'
            or current.timing_precision != 'word'
        )
    )
    if gap_ms is not None and not has_incomparable_internal_timing:
        if gap_ms < 0:
            return 'time_discontinuity'
        if gap_ms > profile.max_continuation_gap_ms:
            return 'long_pause'
        if gap_ms >= profile.terminal_pause_ms and _has_terminal_punctuation(previous):
            return 'terminal_punctuation_pause'

    if current_display_chars + token_display_chars > profile.max_display_chars:
        return 'max_context'
    return None


def _build_utterance(
    raw_snapshot_hash: str,
    profile: SemanticUtteranceProfile,
    tokens: Sequence[RawAsrToken],
    boundary: SemanticUtteranceBoundary,
) -> SemanticUtterance:
    token_ids = tuple(token.token_id for token in tokens)
    source_segment_ids = tuple(dict.fromkeys(token.segment_id for token in tokens))
    identity = {
        'raw_snapshot_hash': raw_snapshot_hash,
        'profile_version': profile.profile_version,
        'token_ids': token_ids,
        'start_boundary': boundary.model_dump(mode='json'),
    }
    return SemanticUtterance(
        utterance_id=f'utterance-{canonical_sha256(identity)}',
        token_ids=token_ids,
        source_segment_ids=source_segment_ids,
        speaker_id=tokens[0].speaker_id,
        speaker_key=tokens[0].speaker_key,
        start_boundary=boundary,
    )


def _speaker_identity(token: RawAsrToken) -> tuple[str | None, str | None] | None:
    if token.speaker_key is None and token.speaker_id is None:
        return None
    return (token.speaker_key, token.speaker_id)


def _gap_ms(previous: RawAsrToken, current: RawAsrToken) -> int | None:
    if previous.end_ms is None or current.start_ms is None:
        return None
    return current.start_ms - previous.end_ms


def _has_terminal_punctuation(token: RawAsrToken) -> bool:
    return (token.text + token.punctuation).rstrip().endswith(_TERMINAL_PUNCTUATION)


def _token_display_char_count(token: RawAsrToken) -> int:
    return sum(
        1
        for character in token.text + token.punctuation
        if not character.isspace()
    )
