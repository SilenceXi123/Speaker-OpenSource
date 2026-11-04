from app.services.lyrics_alignment import attach_lyrics_reference, build_lyrics_reference
from app.services.script_context import extract_script_context, merge_script_contexts


def test_merge_script_contexts_preserves_and_deduplicates_lyrics_references() -> None:
    reference = build_lyrics_reference(
        source_name='歌词.txt',
        extracted_text='点亮心里的光\n我们一起去远方',
    )
    first = attach_lyrics_reference(extract_script_context('点亮心里的光', '歌词.txt'), reference)
    second = attach_lyrics_reference(extract_script_context('普通台本', '台本.txt'), reference)

    merged = merge_script_contexts(first, second)

    assert merged is not None
    assert merged['lyrics_correction_mode'] is True
    assert len(merged['lyrics_references']) == 1
    assert merged['summary']['lyrics_reference_count'] == 1
    assert merged['summary']['lyrics_line_count'] == 2
