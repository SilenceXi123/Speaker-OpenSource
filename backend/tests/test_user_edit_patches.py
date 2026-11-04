from app.services.user_edit_patches import build_segment_edit_patch


def test_build_segment_edit_patch_records_text_and_speaker_changes() -> None:
    before = [
        {'segmentId': 'seg-1', 'speakerKey': 'speaker_0', 'text': '扎记村'},
        {'segmentId': 'seg-2', 'speakerKey': 'speaker_1', 'text': '你好'},
    ]
    after = [
        {'segmentId': 'seg-1', 'speakerKey': 'speaker_0', 'text': '查济村'},
        {'segmentId': 'seg-2', 'speakerKey': 'speaker_2', 'text': '你好'},
    ]

    patch = build_segment_edit_patch(before, after, source='script_correction')

    assert patch['source'] == 'script_correction'
    assert patch['changed_count'] == 2
    assert patch['changes'][0]['field'] == 'text'
    assert patch['changes'][0]['before'] == '扎记村'
    assert patch['changes'][0]['after'] == '查济村'
    assert patch['changes'][1]['field'] == 'speakerKey'


def test_build_segment_edit_patch_returns_empty_patch_without_changes() -> None:
    segments = [{'segmentId': 'seg-1', 'speakerKey': 'speaker_0', 'text': '大家好'}]

    patch = build_segment_edit_patch(segments, segments, source='manual_review')

    assert patch['source'] == 'manual_review'
    assert patch['changed_count'] == 0
    assert patch['changes'] == []
