from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.schemas.subtitle import SubtitleCue
from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.subtitle_atoms import TokenBoundLexicalAtom, build_lexical_atoms
from app.services.subtitle_global_planner import (
    AFFILIATION_LEAD_INS,
    SubtitleCuePlanArtifact,
)
from app.services.subtitle_legal_gaps import SubtitleLegalGap
from app.services.subtitle_pipeline_v2_schema import RawAsrSnapshot, canonical_sha256
from app.services.subtitle_protected_terms import runtime_lexicon_terms
from app.services.subtitle_token_overlays import (
    AuthorizedTextOverlay,
    FillerTokenMask,
    overlay_punctuation,
    render_token_span_text,
)


MAX_SUBTITLE_CHARS = 14
STANDALONE_SINGLE_CJK_FILLERS = set('嗯啊哦哎唉呃额呀哇呜呢嘛哈呵嘿')
_RELIABLE_INFERRED_ATOM_KINDS = frozenset({
    'organization',
    'common_term',
    'ascii_word',
})
_TERMINAL_PUNCTUATION = '。！？!?；;'
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")


@dataclass(frozen=True)
class CueValidationIssue:
    code: str
    severity: str
    message: str
    index: Optional[int] = None
    left_index: Optional[int] = None
    right_index: Optional[int] = None
    term: Optional[str] = None


@dataclass(frozen=True)
class CueValidationReport:
    issues: list[CueValidationIssue] = field(default_factory=list)

    @property
    def has_p0(self) -> bool:
        return any(issue.severity == 'P0' for issue in self.issues)

    @property
    def valid(self) -> bool:
        return not self.has_p0

    def to_dict(self) -> dict[str, object]:
        return {
            'valid': self.valid,
            'has_p0': self.has_p0,
            'issues': [issue.__dict__ for issue in self.issues],
        }


def validate_subtitle_cues(
    cues: list[SubtitleCue],
    *,
    protected_terms: Iterable[str] = (),
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    allow_strong_term_split: bool = False,
    max_cjk_chars: int = MAX_SUBTITLE_CHARS,
) -> CueValidationReport:
    terms = tuple(str(item or '').strip() for item in protected_terms if str(item or '').strip())
    hard_terms = runtime_lexicon_terms(runtime_lexicon_bundle, protection_levels=('hard',))
    strong_terms = runtime_lexicon_terms(runtime_lexicon_bundle, protection_levels=('strong',))
    soft_terms = runtime_lexicon_terms(runtime_lexicon_bundle, protection_levels=('soft',))
    issues: list[CueValidationIssue] = []
    for cue in cues or []:
        clean_text = str(cue.clean_text or '')
        if int(cue.end_ms) <= int(cue.start_ms):
            issues.append(CueValidationIssue(
                code='invalid_timing',
                severity='P0',
                message='字幕时间范围无效',
                index=int(cue.index),
            ))
        if _is_single_cjk_fragment(clean_text):
            issues.append(CueValidationIssue(
                code='isolated_single_cjk_cue',
                severity='P0',
                message='字幕被切成孤立单字',
                index=int(cue.index),
            ))
        if _is_overlong_cjk(clean_text, max_cjk_chars):
            issues.append(CueValidationIssue(
                code='overlong_cjk_cue',
                severity='P1',
                message='中文字幕超过建议字数',
                index=int(cue.index),
            ))

    for left, right in zip(cues or [], (cues or [])[1:]):
        if int(right.start_ms) < int(left.end_ms):
            issues.append(CueValidationIssue(
                code='overlapping_time',
                severity='P0',
                message='相邻字幕时间重叠',
                left_index=int(left.index),
                right_index=int(right.index),
            ))
        issues.extend(_boundary_issues(
            left,
            right,
            terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            hard_terms=hard_terms,
            strong_terms=strong_terms,
            soft_terms=soft_terms,
            allow_strong_term_split=allow_strong_term_split,
        ))
    return CueValidationReport(issues=issues)


def validate_subtitle_cue_plan(
    *,
    raw_snapshot: RawAsrSnapshot,
    overlays: Sequence[AuthorizedTextOverlay],
    masks: Sequence[FillerTokenMask],
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    cue_plan: SubtitleCuePlanArtifact,
    rendered_text_by_cue_id: Mapping[str, str] | None = None,
) -> CueValidationReport:
    issues: list[CueValidationIssue] = []
    _validate_v2_contracts(raw_snapshot, atoms, gaps, cue_plan, issues)
    _validate_v2_boundaries(raw_snapshot, atoms, gaps, cue_plan, issues)
    _validate_v2_overlay_authorization(overlays, atoms, issues)
    expected_rendered = _validate_v2_rendering(
        raw_snapshot,
        overlays,
        masks,
        cue_plan,
        rendered_text_by_cue_id,
        issues,
    )
    _validate_v2_token_tape(raw_snapshot, overlays, cue_plan, issues)
    _validate_v2_timing(raw_snapshot, cue_plan, issues)
    _validate_v2_overflow(atoms, gaps, cue_plan, expected_rendered, issues)
    return CueValidationReport(issues=issues)


def _validate_v2_contracts(
    raw_snapshot: RawAsrSnapshot,
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    cue_plan: SubtitleCuePlanArtifact,
    issues: list[CueValidationIssue],
) -> None:
    try:
        RawAsrSnapshot.model_validate(raw_snapshot.model_dump(mode='json'))
    except (TypeError, ValueError) as exc:
        issues.append(CueValidationIssue(
            code='invalid_raw_snapshot',
            severity='P0',
            message=f'raw snapshot contract failed: {exc}',
        ))
    try:
        SubtitleCuePlanArtifact.model_validate(cue_plan.model_dump(mode='json'))
    except (TypeError, ValueError) as exc:
        issues.append(CueValidationIssue(
            code='invalid_cue_plan_artifact',
            severity='P0',
            message=f'cue plan artifact contract failed: {exc}',
        ))
    raw_token_ids = [token.token_id for token in raw_snapshot.tokens]
    covered_atom_ids: list[str] = []
    seen_atom_ids: set[str] = set()
    expected_atom_start = 0
    for atom in atoms:
        try:
            TokenBoundLexicalAtom.model_validate(atom.model_dump(mode='json'))
        except (TypeError, ValueError) as exc:
            issues.append(CueValidationIssue(
                code='invalid_atom_contract',
                severity='P0',
                message=f'lexical atom contract failed: {exc}',
                term=getattr(atom, 'text', None),
            ))
        if atom.atom_id in seen_atom_ids:
            issues.append(CueValidationIssue(
                code='duplicate_atom_id',
                severity='P0',
                message='lexical atom IDs must be unique',
                term=atom.text,
            ))
        seen_atom_ids.add(atom.atom_id)
        if atom.token_start != expected_atom_start or atom.token_end > len(raw_token_ids):
            issues.append(CueValidationIssue(
                code='atom_tape_mismatch',
                severity='P0',
                message='lexical atoms do not cover the raw token tape in order',
                term=atom.text,
            ))
        if 0 <= atom.token_start < atom.token_end <= len(raw_token_ids):
            expected_ids = tuple(raw_token_ids[atom.token_start:atom.token_end])
            if atom.token_ids != expected_ids:
                issues.append(CueValidationIssue(
                    code='atom_tape_mismatch',
                    severity='P0',
                    message='lexical atom token IDs disagree with raw facts',
                    term=atom.text,
                ))
        identity = {
            'raw_snapshot_hash': raw_snapshot.snapshot_hash,
            'utterance_id': atom.utterance_id,
            'token_ids': atom.token_ids,
            'text': atom.text,
            'protection': atom.protection,
            'source': atom.source,
            'evidence_refs': atom.evidence_refs,
        }
        if atom.atom_id != f'atom-{canonical_sha256(identity)}':
            issues.append(CueValidationIssue(
                code='invalid_atom_id',
                severity='P0',
                message='lexical atom ID does not match its content',
                term=atom.text,
            ))
        covered_atom_ids.extend(atom.token_ids)
        expected_atom_start = atom.token_end
    if covered_atom_ids != raw_token_ids:
        issues.append(CueValidationIssue(
            code='atom_tape_mismatch',
            severity='P0',
            message='lexical atoms do not conserve the raw token tape',
        ))

    actual_gap_pairs: list[tuple[str, str]] = []
    seen_gap_ids: set[str] = set()
    for gap in gaps:
        try:
            SubtitleLegalGap.model_validate(gap.model_dump(mode='json'))
        except (TypeError, ValueError) as exc:
            issues.append(CueValidationIssue(
                code='invalid_gap_contract',
                severity='P0',
                message=f'legal gap contract failed: {exc}',
            ))
        if gap.gap_id in seen_gap_ids:
            issues.append(CueValidationIssue(
                code='duplicate_gap_id',
                severity='P0',
                message='legal gap IDs must be unique',
            ))
        seen_gap_ids.add(gap.gap_id)
        identity = {
            'raw_snapshot_hash': raw_snapshot.snapshot_hash,
            'utterance_id': gap.utterance_id,
            'left_token_id': gap.left_token_id,
            'right_token_id': gap.right_token_id,
        }
        if gap.gap_id != f'gap-{canonical_sha256(identity)}':
            issues.append(CueValidationIssue(
                code='invalid_gap_id',
                severity='P0',
                message='legal gap ID does not match its content',
            ))
        actual_gap_pairs.append((gap.left_token_id, gap.right_token_id))
    expected_gap_pairs = list(zip(raw_token_ids, raw_token_ids[1:]))
    if actual_gap_pairs != expected_gap_pairs or any(
        gap.left_token_index != index - 1 or gap.right_token_index != index
        for index, gap in enumerate(gaps, start=1)
    ):
        issues.append(CueValidationIssue(
            code='gap_tape_mismatch',
            severity='P0',
            message='legal gaps do not cover every adjacent raw token pair in order',
        ))

    if cue_plan.raw_snapshot_hash != raw_snapshot.snapshot_hash:
        issues.append(CueValidationIssue(
            code='raw_snapshot_mismatch',
            severity='P0',
            message='cue plan references a different raw snapshot',
        ))
    if cue_plan.raw_token_count != len(raw_snapshot.tokens):
        issues.append(CueValidationIssue(
            code='raw_token_count_mismatch',
            severity='P0',
            message='cue plan raw token count disagrees with the snapshot',
        ))
    atoms_hash = canonical_sha256([atom.model_dump(mode='json') for atom in atoms])
    if cue_plan.atoms_hash != atoms_hash:
        issues.append(CueValidationIssue(
            code='atoms_hash_mismatch',
            severity='P0',
            message='cue plan lexical atom hash disagrees with validator input',
        ))
    gaps_hash = canonical_sha256([gap.model_dump(mode='json') for gap in gaps])
    if cue_plan.gaps_hash != gaps_hash:
        issues.append(CueValidationIssue(
            code='gaps_hash_mismatch',
            severity='P0',
            message='cue plan legal gap hash disagrees with validator input',
        ))


def _validate_v2_token_tape(
    raw_snapshot: RawAsrSnapshot,
    overlays: Sequence[AuthorizedTextOverlay],
    cue_plan: SubtitleCuePlanArtifact,
    issues: list[CueValidationIssue],
) -> None:
    expected = [token.token_id for token in raw_snapshot.tokens]
    expected_set = set(expected)
    actual = [token_id for cue in cue_plan.cues for token_id in cue.token_ids]
    actual_counts = Counter(actual)
    if any(count > 1 for count in actual_counts.values()):
        issues.append(CueValidationIssue(
            code='token_duplicate',
            severity='P0',
            message='cue plan repeats raw token IDs',
        ))
    if any(token_id not in actual_counts for token_id in expected):
        issues.append(CueValidationIssue(
            code='token_loss',
            severity='P0',
            message='cue plan drops raw token IDs',
        ))
    if any(token_id not in expected_set for token_id in actual):
        issues.append(CueValidationIssue(
            code='unknown_token_reference',
            severity='P0',
            message='cue plan references token IDs outside the raw snapshot',
        ))
    if Counter(actual) == Counter(expected) and actual != expected:
        issues.append(CueValidationIssue(
            code='token_reorder',
            severity='P0',
            message='cue plan reorders the raw token tape',
        ))

    expected_start = 0
    for index, cue in enumerate(cue_plan.cues):
        if cue.token_start != expected_start or cue.token_end > len(expected):
            issues.append(CueValidationIssue(
                code='token_range_gap',
                severity='P0',
                message='cue token ranges are not contiguous and ordered',
                index=index,
            ))
        if 0 <= cue.token_start < cue.token_end <= len(expected):
            expected_ids = tuple(expected[cue.token_start:cue.token_end])
            if cue.token_ids != expected_ids:
                issues.append(CueValidationIssue(
                    code='token_range_mismatch',
                    severity='P0',
                    message='cue token IDs disagree with its raw token range',
                    index=index,
                ))
            overlay_by_start = {
                overlay.token_start: overlay
                for overlay in overlays
                if cue.token_start <= overlay.token_start < cue.token_end
            }
            display_parts: list[str] = []
            token_index = cue.token_start
            while token_index < cue.token_end:
                overlay = overlay_by_start.get(token_index)
                if overlay is not None and overlay.token_end <= cue.token_end:
                    display_parts.append(
                        overlay.replacement_text
                        + overlay_punctuation(raw_snapshot.tokens[
                            overlay.token_start:overlay.token_end
                        ])
                    )
                    token_index = overlay.token_end
                    continue
                token = raw_snapshot.tokens[token_index]
                display_parts.append(token.text + token.punctuation)
                token_index += 1
            char_count = sum(
                1
                for part in display_parts
                for character in part
                if not character.isspace()
            )
            word_count = sum(len(_WORD_RE.findall(part)) for part in display_parts)
            language_mode = cue_plan.profile.language_mode
            expected_language_mode = (
                language_mode
                if language_mode != 'auto'
                else (
                    'english_word'
                    if raw_snapshot.language.strip().lower().startswith('en')
                    else 'cjk_character'
                )
            )
            if cue.language_mode != expected_language_mode:
                issues.append(CueValidationIssue(
                    code='language_mode_mismatch',
                    severity='P0',
                    message='cue language mode disagrees with the planner profile and raw facts',
                    index=index,
                ))
            unit_count = word_count if cue.language_mode == 'english_word' else char_count
            if (
                cue.display_char_count != char_count
                or cue.display_word_count != word_count
                or cue.display_unit_count != unit_count
            ):
                issues.append(CueValidationIssue(
                    code='display_fact_mismatch',
                    severity='P0',
                    message='cue display counts disagree with its authorized rendered span',
                    index=index,
                ))
        expected_start = cue.token_end
    if expected_start != len(expected):
        issues.append(CueValidationIssue(
            code='token_range_gap',
            severity='P0',
            message='cue token ranges do not cover the complete raw token tape',
        ))


def _validate_v2_boundaries(
    raw_snapshot: RawAsrSnapshot,
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    cue_plan: SubtitleCuePlanArtifact,
    issues: list[CueValidationIssue],
) -> None:
    gaps_by_right = {gap.right_token_index: gap for gap in gaps}
    atoms_by_boundary: dict[int, list[TokenBoundLexicalAtom]] = {}
    for atom in atoms:
        for boundary in range(atom.token_start + 1, atom.token_end):
            atoms_by_boundary.setdefault(boundary, []).append(atom)
    for index, cue in enumerate(cue_plan.cues):
        if not (0 <= cue.token_start < cue.token_end <= len(raw_snapshot.tokens)):
            continue
        tokens = raw_snapshot.tokens[cue.token_start:cue.token_end]
        speakers = {(token.speaker_id, token.speaker_key) for token in tokens}
        if len(speakers) != 1:
            issues.append(CueValidationIssue(
                code='cross_speaker_cue',
                severity='P0',
                message='cue crosses raw speaker evidence',
                index=index,
            ))
        elif next(iter(speakers)) != (cue.speaker_id, cue.speaker_key):
            issues.append(CueValidationIssue(
                code='speaker_evidence_mismatch',
                severity='P0',
                message='cue speaker evidence disagrees with its raw tokens',
                index=index,
            ))
        if index == 0:
            continue
        boundary = cue.token_start
        gap = gaps_by_right.get(boundary)
        left = raw_snapshot.tokens[boundary - 1]
        right = raw_snapshot.tokens[boundary]
        speaker_changed = (left.speaker_id, left.speaker_key) != (
            right.speaker_id,
            right.speaker_key,
        )
        if gap is None or cue.start_gap_id != gap.gap_id:
            issues.append(CueValidationIssue(
                code='illegal_gap_boundary',
                severity='P0',
                message='cue boundary does not reference its exact adjacent token gap',
                index=index,
            ))
        elif speaker_changed:
            if (
                cue.start_boundary_kind != 'required_speaker'
                or gap.disposition != 'required'
            ):
                issues.append(CueValidationIssue(
                    code='illegal_gap_boundary',
                    severity='P0',
                    message='speaker transition lacks a required cue boundary',
                    index=index,
                ))
        elif (
            cue.start_boundary_kind != 'planned_legal_gap'
            or gap.disposition != 'allowed'
            or not gap.legal_gap
        ):
            issues.append(CueValidationIssue(
                code='illegal_gap_boundary',
                severity='P0',
                message='ordinary cue boundary is not an allowed legal gap',
                index=index,
            ))

        for atom in atoms_by_boundary.get(boundary, ()):
            severity = 'P0' if atom.protection == 'hard' else 'P1'
            issues.append(CueValidationIssue(
                code='hard_atom_split' if severity == 'P0' else 'protected_atom_split',
                severity=severity,
                message='cue boundary splits a token-bound lexical atom',
                index=index,
                term=atom.text,
            ))


def _validate_v2_overlay_authorization(
    overlays: Sequence[AuthorizedTextOverlay],
    atoms: Sequence[TokenBoundLexicalAtom],
    issues: list[CueValidationIssue],
) -> None:
    accepted_atoms = [atom for atom in atoms if atom.source == 'accepted_script_correction']
    matched_atom_ids: set[str] = set()
    for overlay in overlays:
        matching = next((
            atom
            for atom in accepted_atoms
            if atom.token_start == overlay.token_start
            and atom.token_end == overlay.token_end
            and atom.token_ids == overlay.token_ids
            and atom.text == overlay.replacement_text
            and overlay.candidate_id in atom.evidence_refs
        ), None)
        if matching is None:
            issues.append(CueValidationIssue(
                code='unauthorized_text_insert',
                severity='P0',
                message='text overlay is not backed by an accepted correction atom',
                term=overlay.replacement_text,
            ))
        else:
            matched_atom_ids.add(matching.atom_id)
    for atom in accepted_atoms:
        if atom.atom_id not in matched_atom_ids:
            issues.append(CueValidationIssue(
                code='missing_authorized_overlay',
                severity='P0',
                message='accepted correction atom has no matching rendered overlay',
                term=atom.text,
            ))


def _validate_v2_rendering(
    raw_snapshot: RawAsrSnapshot,
    overlays: Sequence[AuthorizedTextOverlay],
    masks: Sequence[FillerTokenMask],
    cue_plan: SubtitleCuePlanArtifact,
    rendered_text_by_cue_id: Mapping[str, str] | None,
    issues: list[CueValidationIssue],
) -> dict[str, str]:
    expected: dict[str, str] = {}
    if raw_snapshot.tokens:
        try:
            render_token_span_text(
                raw_snapshot=raw_snapshot,
                token_start=0,
                token_end=len(raw_snapshot.tokens),
                overlays=overlays,
                masks=masks,
            )
        except (TypeError, ValueError) as exc:
            issues.append(CueValidationIssue(
                code='invalid_render_evidence',
                severity='P0',
                message=f'render evidence failed validation: {exc}',
            ))
            overlays = ()
            masks = ()
    elif overlays or masks:
        issues.append(CueValidationIssue(
            code='invalid_render_evidence',
            severity='P0',
            message='empty raw snapshot cannot contain render evidence',
        ))
        overlays = ()
        masks = ()

    overlay_by_start = {overlay.token_start: overlay for overlay in overlays}
    overlay_by_token = {
        token_index: overlay
        for overlay in overlays
        for token_index in range(overlay.token_start, overlay.token_end)
    }
    masked_indexes = {mask.token_index for mask in masks}
    for index, cue in enumerate(cue_plan.cues):
        if not (0 <= cue.token_start < cue.token_end <= len(raw_snapshot.tokens)):
            continue
        left_overlay = overlay_by_token.get(cue.token_start)
        right_overlay = overlay_by_token.get(cue.token_end - 1)
        if (
            left_overlay is not None
            and left_overlay.token_start < cue.token_start
        ) or (
            right_overlay is not None
            and right_overlay.token_end > cue.token_end
        ):
            issues.append(CueValidationIssue(
                code='invalid_render_evidence',
                severity='P0',
                message='cue boundary cuts an authorized text overlay',
                index=index,
            ))
            continue
        parts: list[str] = []
        token_index = cue.token_start
        while token_index < cue.token_end:
            overlay = overlay_by_start.get(token_index)
            if overlay is not None:
                parts.append(overlay.replacement_text)
                parts.append(overlay_punctuation(raw_snapshot.tokens[
                    overlay.token_start:overlay.token_end
                ]))
                token_index = overlay.token_end
                continue
            if token_index not in masked_indexes:
                token = raw_snapshot.tokens[token_index]
                parts.append(token.text + token.punctuation)
            token_index += 1
        expected[cue.cue_id] = normalize_to_simplified_chinese(''.join(parts))

    if rendered_text_by_cue_id is None:
        return expected
    cue_ids = {cue.cue_id for cue in cue_plan.cues}
    for cue_id in cue_ids - set(rendered_text_by_cue_id):
        issues.append(CueValidationIssue(
            code='missing_rendered_cue_text',
            severity='P0',
            message=f'final rendered text is missing for cue {cue_id}',
        ))
    for cue_id in set(rendered_text_by_cue_id) - cue_ids:
        issues.append(CueValidationIssue(
            code='unknown_rendered_cue_text',
            severity='P0',
            message=f'final rendered text references unknown cue {cue_id}',
        ))
    for index, cue in enumerate(cue_plan.cues):
        if cue.cue_id not in rendered_text_by_cue_id:
            continue
        actual = str(rendered_text_by_cue_id[cue.cue_id])
        if actual != normalize_to_simplified_chinese(actual):
            issues.append(CueValidationIssue(
                code='traditional_chinese_output',
                severity='P0',
                message='final rendered cue still contains traditional Chinese',
                index=index,
            ))
        if cue.cue_id in expected and actual != expected[cue.cue_id]:
            issues.append(CueValidationIssue(
                code='unauthorized_text_insert',
                severity='P0',
                message='final rendered cue differs from raw tokens and authorized edits',
                index=index,
            ))
    return expected


def _validate_v2_timing(
    raw_snapshot: RawAsrSnapshot,
    cue_plan: SubtitleCuePlanArtifact,
    issues: list[CueValidationIssue],
) -> None:
    for index, cue in enumerate(cue_plan.cues):
        if not (0 <= cue.token_start < cue.token_end <= len(raw_snapshot.tokens)):
            continue
        first = raw_snapshot.tokens[cue.token_start]
        last = raw_snapshot.tokens[cue.token_end - 1]
        if (
            first.start_ms is None
            or last.end_ms is None
            or last.end_ms < first.start_ms
        ):
            expected = (None, None, None)
        else:
            expected = (
                first.start_ms,
                last.end_ms,
                last.end_ms - first.start_ms,
            )
        actual = (cue.start_ms, cue.end_ms, cue.duration_ms)
        if actual != expected:
            issues.append(CueValidationIssue(
                code='cumulative_timing_drift',
                severity='P0',
                message='cue timing is not derived directly from its first/last raw token',
                index=index,
            ))
        if cue.start_ms is not None and cue.end_ms is not None and cue.end_ms <= cue.start_ms:
            issues.append(CueValidationIssue(
                code='invalid_timing',
                severity='P0',
                message='cue time range is empty or reversed',
                index=index,
            ))
    for left_index, (left, right) in enumerate(
        zip(cue_plan.cues, cue_plan.cues[1:]),
    ):
        if (
            left.end_ms is not None
            and right.start_ms is not None
            and right.start_ms < left.end_ms
        ):
            issues.append(CueValidationIssue(
                code='overlapping_time',
                severity='P0',
                message='adjacent cue time ranges overlap',
                left_index=left_index,
                right_index=left_index + 1,
            ))


def _validate_v2_overflow(
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    cue_plan: SubtitleCuePlanArtifact,
    rendered_text_by_cue_id: Mapping[str, str],
    issues: list[CueValidationIssue],
) -> None:
    gaps_by_right = {gap.right_token_index: gap for gap in gaps}
    atoms_by_range = {
        (atom.token_start, atom.token_end): atom
        for atom in atoms
    }
    for index, cue in enumerate(cue_plan.cues):
        text = rendered_text_by_cue_id.get(cue.cue_id)
        if text is None:
            continue
        limit = (
            cue_plan.profile.strict_max_display_units
            if cue_plan.profile.mode == 'strict'
            else (
                cue_plan.profile.english_safety_max_words
                if cue.language_mode == 'english_word'
                else cue_plan.profile.safety_max_display_units
            )
        )
        if limit is None:
            continue
        units = _v2_display_units(text, cue.language_mode)
        if units <= limit:
            if cue.strict_overflow_reason is not None:
                issues.append(CueValidationIssue(
                    code='stale_overflow_reason',
                    severity='P1',
                    message='rendered cue is in range but still carries an overflow reason',
                    index=index,
                ))
            continue

        atom = atoms_by_range.get((cue.token_start, cue.token_end))
        unbreakable_term = (
            atom.text
            if atom is not None
            and (
                atom.protection == 'hard'
                or atom.token_end - atom.token_start == 1
            )
            and _v2_display_units(atom.text, cue.language_mode) > limit
            else None
        )
        if unbreakable_term is None:
            unbreakable_term = next((
                text
                for organization in atoms
                if organization.source == 'organization'
                and organization.protection == 'hard'
                and cue.token_start < organization.token_start
                and organization.token_end == cue.token_end
                and text.endswith(organization.text)
                and text[:-len(organization.text)].strip() in AFFILIATION_LEAD_INS
            ), None)
        has_legal_gap = any(
            gap is not None
            and gap.disposition == 'allowed'
            and gap.legal_gap
            for gap in (
                gaps_by_right.get(boundary)
                for boundary in range(cue.token_start + 1, cue.token_end)
            )
        )
        terminal_punctuation_overflow = (
            cue_plan.profile.mode == 'smart'
            and units == limit + 1
            and text.rstrip().endswith(tuple(_TERMINAL_PUNCTUATION))
        )
        if unbreakable_term is not None:
            reason = 'unbreakable_atom'
        elif not has_legal_gap:
            reason = 'no_legal_gap'
        elif terminal_punctuation_overflow:
            reason = 'terminal_punctuation'
        else:
            issues.append(CueValidationIssue(
                code=(
                    'avoidable_strict_overflow'
                    if cue_plan.profile.mode == 'strict'
                    else 'avoidable_length_overflow'
                ),
                severity='P0',
                message='cue exceeds its display limit despite an available legal gap',
                index=index,
            ))
            continue
        if cue.strict_overflow_reason != reason:
            issues.append(CueValidationIssue(
                code='invalid_overflow_reason',
                severity='P0',
                message=f'cue overflow must carry reason {reason}',
                index=index,
            ))
            continue
        issues.append(CueValidationIssue(
            code='unavoidable_overflow',
            severity='P1',
            message=f'cue exceeds its display limit: {reason}',
            index=index,
            term=unbreakable_term,
        ))


def _v2_display_units(text: str, language_mode: str) -> int:
    if language_mode == 'english_word':
        return len(_WORD_RE.findall(text))
    return sum(1 for character in text if not character.isspace())


def _boundary_issues(
    left: SubtitleCue,
    right: SubtitleCue,
    protected_terms: tuple[str, ...],
    *,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    hard_terms: tuple[str, ...] = (),
    strong_terms: tuple[str, ...] = (),
    soft_terms: tuple[str, ...] = (),
    allow_strong_term_split: bool = False,
) -> list[CueValidationIssue]:
    left_text = str(left.clean_text or '')
    right_text = str(right.clean_text or '')
    if not left_text or not right_text:
        return []
    combined = left_text + right_text
    boundary = len(left_text)
    issues: list[CueValidationIssue] = []
    for term in hard_terms:
        if _term_is_split_at_boundary(combined, boundary, term):
            return [CueValidationIssue(
                code='lexicon_hard_term_split',
                severity='P0',
                message='字幕边界切进运行时硬保护词',
                left_index=int(left.index),
                right_index=int(right.index),
                term=term,
            )]

    for term in protected_terms:
        start = combined.find(term)
        while start != -1:
            end = start + len(term)
            if start < boundary < end:
                issues.append(CueValidationIssue(
                    code='protected_term_split',
                    severity='P0',
                    message='字幕边界切进受保护词',
                    left_index=int(left.index),
                    right_index=int(right.index),
                    term=term,
                ))
                return issues
            start = combined.find(term, start + 1)

    if not allow_strong_term_split:
        for term in strong_terms:
            if _term_is_split_at_boundary(combined, boundary, term):
                issues.append(CueValidationIssue(
                    code='lexicon_strong_term_split',
                    severity='P1',
                    message='字幕边界切进运行时强保护词',
                    left_index=int(left.index),
                    right_index=int(right.index),
                    term=term,
                ))
                break

    if _needs_ascii_space(left_text[-1], right_text[0]):
        return issues
    if _is_repeated_phrase_transition_boundary(combined, boundary):
        return issues

    for atom in build_lexical_atoms(
        combined,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        if atom.start < boundary < atom.end:
            if atom.kind in {'runtime_lexicon_strong', 'runtime_lexicon_soft'}:
                continue
            if atom.text in strong_terms or atom.text in soft_terms:
                continue
            if atom.kind not in _RELIABLE_INFERRED_ATOM_KINDS:
                continue
            issues.append(CueValidationIssue(
                code='lexical_atom_split',
                severity='P0',
                message='字幕边界切进词法原子',
                left_index=int(left.index),
                right_index=int(right.index),
                term=atom.text,
            ))
            break
    return issues


def _term_is_split_at_boundary(text: str, boundary: int, term: str) -> bool:
    if not term:
        return False
    start = text.find(term)
    while start != -1:
        end = start + len(term)
        if start < boundary < end:
            return True
        start = text.find(term, start + 1)
    return False


def _is_single_cjk_fragment(text: str) -> bool:
    return len(text) == 1 and _is_cjk_char(text) and text not in STANDALONE_SINGLE_CJK_FILLERS


def _is_overlong_cjk(text: str, max_cjk_chars: int) -> bool:
    if any(char.isascii() and char.isalnum() for char in text):
        return False
    return len(text) > max_cjk_chars


def _is_repeated_phrase_transition_boundary(text: str, boundary: int) -> bool:
    if boundary < 3 or boundary >= len(text):
        return False
    left = text[boundary - 3:boundary]
    right_char = text[boundary]
    return (
        len(left) == 3
        and left[1] == '一'
        and left[0] == left[2] == right_char
        and _is_cjk_char(right_char)
    )


def _is_cjk_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return 0x3400 <= codepoint <= 0x4DBF or 0x4E00 <= codepoint <= 0x9FFF or 0xF900 <= codepoint <= 0xFAFF


def _needs_ascii_space(left_char: str, right_char: str) -> bool:
    return bool(left_char and right_char and left_char.isascii() and right_char.isascii() and left_char.isalnum() and right_char.isalnum())
