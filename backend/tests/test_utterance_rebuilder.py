from app.services.utterance_rebuilder import rebuild_semantic_utterances


def test_rebuild_semantic_utterances_reattaches_short_tail_before_subtitle_enhancer() -> None:
    segments = [
        {'segmentId': 's0', 'speakerKey': 'speaker_0', 'startMs': 19000, 'endMs': 22000, 'text': '这里就是沈阳的国际赛艇', 'words': []},
        {'segmentId': 's1', 'speakerKey': 'speaker_1', 'startMs': 22020, 'endMs': 22800, 'text': '中心哦', 'words': []},
        {'segmentId': 's2', 'speakerKey': 'speaker_1', 'startMs': 23000, 'endMs': 23800, 'text': '听说过', 'words': []},
    ]

    rebuilt = rebuild_semantic_utterances(segments)

    assert [(item['speakerKey'], item['text']) for item in rebuilt] == [
        ('speaker_0', '这里就是沈阳的国际赛艇中心哦'),
        ('speaker_1', '听说过'),
    ]
    assert rebuilt[0]['sourceSegmentIds'] == ['s0', 's1']
    assert 'short_tail_reassign' in rebuilt[0]['repairTags']


def test_rebuild_semantic_utterances_merges_same_speaker_continuation() -> None:
    segments = [
        {'segmentId': 's3', 'speakerKey': 'speaker_0', 'startMs': 23800, 'endMs': 26000, 'text': '它是国内少有的集这个专业训练', 'words': []},
        {'segmentId': 's4', 'speakerKey': 'speaker_0', 'startMs': 26020, 'endMs': 30000, 'text': '大众培训和全民的水上休闲运动', 'words': []},
        {'segmentId': 's5', 'speakerKey': 'speaker_0', 'startMs': 30020, 'endMs': 33000, 'text': '于一体的多功能综合性水上', 'words': []},
        {'segmentId': 's6', 'speakerKey': 'speaker_0', 'startMs': 33020, 'endMs': 34500, 'text': '运动中心', 'words': []},
    ]

    rebuilt = rebuild_semantic_utterances(segments)

    assert len(rebuilt) == 1
    assert rebuilt[0]['speakerKey'] == 'speaker_0'
    assert rebuilt[0]['text'] == '它是国内少有的集这个专业训练大众培训和全民的水上休闲运动于一体的多功能综合性水上运动中心'
    assert rebuilt[0]['sourceSegmentIds'] == ['s3', 's4', 's5', 's6']
    assert 'same_speaker_continuation' in rebuilt[0]['repairTags']


def test_rebuild_semantic_utterances_keeps_independent_short_reply() -> None:
    segments = [
        {'segmentId': 's0', 'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 900, 'text': '你听说过吗', 'words': []},
        {'segmentId': 's1', 'speakerKey': 'speaker_1', 'startMs': 950, 'endMs': 1500, 'text': '听说过', 'words': []},
    ]

    rebuilt = rebuild_semantic_utterances(segments)

    assert [(item['speakerKey'], item['text']) for item in rebuilt] == [
        ('speaker_0', '你听说过吗'),
        ('speaker_1', '听说过'),
    ]
