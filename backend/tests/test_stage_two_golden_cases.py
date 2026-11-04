from tests.fixtures.golden_subtitle_cases import GOLDEN_CASES

from app.services.subtitle_cue_validator import validate_subtitle_cues
from app.services.subtitles import build_subtitle_cues


def test_stage_two_golden_cases_have_no_p0_and_preserve_terms() -> None:
    for case in GOLDEN_CASES:
        cues = build_subtitle_cues(
            case['segments'],
            {'speaker_0': '甲', 'speaker_1': '乙'},
            export_frame_rate='25',
            script_context=case.get('script_context'),
        )
        content = ''.join(cue.clean_text for cue in cues)
        report = validate_subtitle_cues(
            cues,
            protected_terms=tuple(case.get('script_context', {}).get('terms') or ()),
        )

        assert not report.has_p0, case['name']
        for text in case['expected_text_contains']:
            assert text in content, case['name']
        _assert_no_forbidden_boundary_splits(cues, case)


def _assert_no_forbidden_boundary_splits(cues, case: dict) -> None:
    cue_texts = [str(cue.clean_text or '') for cue in cues]
    for left, right in case.get('forbidden_boundary_splits') or []:
        for index in range(len(cue_texts) - 1):
            assert not (
                cue_texts[index].endswith(left)
                and cue_texts[index + 1].startswith(right)
            ), case['name']
