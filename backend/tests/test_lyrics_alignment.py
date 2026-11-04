from app.services.lyrics_alignment import (
    apply_lyrics_correction_mode,
    attach_lyrics_reference,
    build_lyrics_reference,
)


def _segment(segment_id: str, start_ms: int, end_ms: int, text: str) -> dict:
    return {
        'segmentId': segment_id,
        'speakerKey': 'speaker_1',
        'speakerId': 1,
        'startMs': start_ms,
        'endMs': end_ms,
        'text': text,
        'subtitleParts': [text],
        'v2FinalCue': True,
    }


def test_build_timed_srt_lyrics_reference_preserves_exact_text_and_timing() -> None:
    payload = '''1
00:00:10,000 --> 00:00:11,500
We never give up

2
00:00:12,000 --> 00:00:13,250
一路冲到最后
'''.encode()

    reference = build_lyrics_reference(
        source_name='主题曲.srt',
        extracted_text='We never give up\n一路冲到最后',
        raw_payload=payload,
    )

    assert reference is not None
    assert reference['timing_mode'] == 'timed_srt'
    assert reference['line_count'] == 2
    assert reference['lines'][0]['text'] == 'We never give up'
    assert reference['lines'][0]['start_ms'] == 10_000
    assert reference['lines'][1]['end_ms'] == 13_250


def test_untimed_lyrics_preserve_source_text_and_ignore_song_metadata() -> None:
    raw = '作词：导演组\n[Verse 1]\n點亮心裡的光\n【副歌】\nWe Never Give Up'.encode()

    reference = build_lyrics_reference(
        source_name='主题曲.txt',
        extracted_text='作词：导演组\n[Verse 1]\n点亮心里的光\n【副歌】\nWe Never Give Up',
        raw_payload=raw,
    )

    assert reference is not None
    assert [line['text'] for line in reference['lines']] == [
        '點亮心裡的光',
        'We Never Give Up',
    ]


def test_timed_lyrics_replace_song_window_and_preserve_dialogue() -> None:
    reference = build_lyrics_reference(
        source_name='主题曲.srt',
        extracted_text='',
        raw_payload='''1
00:00:10,000 --> 00:00:11,500
We never give up

2
00:00:12,000 --> 00:00:13,250
一路冲到最后
'''.encode(),
    )
    context = attach_lyrics_reference({'source_name': '主题曲.srt'}, reference)
    segments = [
        _segment('dialogue-before', 0, 5_000, '欢迎来到节目'),
        _segment('wrong-song-1', 10_000, 11_600, '为拿给发'),
        _segment('wrong-song-2', 11_900, 13_400, '一路从走'),
        _segment('dialogue-after', 20_000, 23_000, '接下来进入点评'),
    ]

    result = apply_lyrics_correction_mode(segments, context, source_duration_ms=30_000)

    assert [item['text'] for item in result.segments] == [
        '欢迎来到节目',
        'We never give up',
        '一路冲到最后',
        '接下来进入点评',
    ]
    assert result.report['status'] == 'applied'
    assert result.report['replaced_segment_count'] == 2
    assert result.report['applied_line_count'] == 2
    lyric_segments = [item for item in result.segments if item.get('lyricsCorrectionMode')]
    assert all(item['timingEvidence'] == 'lyrics_srt' for item in lyric_segments)
    assert lyric_segments[0]['lyricsSourceName'] == '主题曲.srt'


def test_timed_lyrics_do_not_remove_dialogue_between_distant_lyric_cues() -> None:
    reference = build_lyrics_reference(
        source_name='主题曲.srt',
        extracted_text='',
        raw_payload='''1
00:00:10,000 --> 00:00:11,000
第一句歌词

2
00:00:17,000 --> 00:00:18,000
第二句歌词
'''.encode(),
    )
    context = attach_lyrics_reference({}, reference)
    segments = [
        _segment('song-1', 10_000, 11_000, '第一句错词'),
        _segment('dialogue', 13_000, 15_000, '中间保留这句对白'),
        _segment('song-2', 17_000, 18_000, '第二句错词'),
    ]

    result = apply_lyrics_correction_mode(segments, context)

    assert [item['text'] for item in result.segments] == [
        '第一句歌词',
        '中间保留这句对白',
        '第二句歌词',
    ]


def test_song_relative_srt_is_located_in_episode_and_warped_by_multiple_anchors() -> None:
    reference = build_lyrics_reference(
        source_name='节目歌曲.srt',
        extracted_text='',
        raw_payload='''1
00:00:00,000 --> 00:00:01,500
点亮心里的光

2
00:00:03,000 --> 00:00:04,500
穿过黑夜和风浪

3
00:00:06,000 --> 00:00:07,500
我们一起去远方
'''.encode(),
    )
    context = attach_lyrics_reference({}, reference)
    segments = [
        _segment('dialogue-before', 0, 3_000, '欢迎来到今天的节目'),
        _segment('song-1', 100_000, 101_500, '点亮心里的光'),
        _segment('song-2', 104_000, 105_500, '穿过黑夜和风浪'),
        _segment('song-3', 108_000, 109_500, '我们一起去远方'),
        _segment('dialogue-after', 115_000, 118_000, '谢谢你们的表演'),
    ]

    result = apply_lyrics_correction_mode(segments, context, source_duration_ms=120_000)

    lyric_segments = [item for item in result.segments if item.get('lyricsCorrectionMode')]
    assert [item['text'] for item in lyric_segments] == [
        '点亮心里的光',
        '穿过黑夜和风浪',
        '我们一起去远方',
    ]
    assert [item['startMs'] for item in lyric_segments] == [100_000, 103_750, 107_750]
    assert result.report['applications'][0]['alignment_method'] == 'relative_srt_anchor_warp'
    assert result.report['applications'][0]['offset_ms'] == 101_000
    assert result.report['alignment_anchor_count'] == 3
    assert '欢迎来到今天的节目' in [item['text'] for item in result.segments]
    assert '谢谢你们的表演' in [item['text'] for item in result.segments]


def test_unrelated_song_reference_is_skipped_in_long_episode() -> None:
    unrelated = build_lyrics_reference(
        source_name='另一首歌.srt',
        extracted_text='',
        raw_payload='''1
00:00:00,000 --> 00:00:01,000
完全不同的歌词

2
00:00:02,000 --> 00:00:03,000
这里也没有唱过

3
00:00:04,000 --> 00:00:05,000
不能覆盖开场对白
'''.encode(),
    )
    context = attach_lyrics_reference({}, unrelated)
    segments = [
        _segment('opening', 0, 4_000, '欢迎来到节目现场'),
        _segment('dialogue', 600_000, 604_000, '接下来请听评委点评'),
    ]

    result = apply_lyrics_correction_mode(segments, context, source_duration_ms=1_200_000)

    assert result.segments == segments
    assert result.report['status'] == 'not_applied'
    assert result.report['warnings']


def test_long_unmatched_studio_intro_does_not_overwrite_episode_dialogue() -> None:
    reference = build_lyrics_reference(
        source_name='带长前奏的歌曲.srt',
        extracted_text='',
        raw_payload='''1
00:00:00,000 --> 00:00:05,000
录音室版本独有的长前奏

2
00:00:20,000 --> 00:00:21,500
点亮心里的光

3
00:00:23,000 --> 00:00:24,500
穿过黑夜和风浪

4
00:00:26,000 --> 00:00:27,500
我们一起去远方
'''.encode(),
    )
    context = attach_lyrics_reference({}, reference)
    segments = [
        _segment('dialogue', 80_000, 88_000, '这段对白必须保留'),
        _segment('song-1', 100_000, 101_500, '点亮心里的光'),
        _segment('song-2', 104_000, 105_500, '穿过黑夜和风浪'),
        _segment('song-3', 108_000, 109_500, '我们一起去远方'),
    ]

    result = apply_lyrics_correction_mode(segments, context, source_duration_ms=120_000)

    texts = [item['text'] for item in result.segments]
    lyric_texts = [item['text'] for item in result.segments if item.get('lyricsCorrectionMode')]
    assert '这段对白必须保留' in texts
    assert '录音室版本独有的长前奏' not in texts
    assert lyric_texts == ['点亮心里的光', '穿过黑夜和风浪', '我们一起去远方']


def test_single_late_anchor_cannot_shift_the_remaining_song() -> None:
    reference = build_lyrics_reference(
        source_name='异常锚点歌曲.srt',
        extracted_text='',
        raw_payload='''1
00:00:00,000 --> 00:00:01,000
点亮心里的光

2
00:00:10,000 --> 00:00:11,000
穿过黑夜和风浪

3
00:00:20,000 --> 00:00:21,000
我们一起去远方

4
00:00:30,000 --> 00:00:31,000
继续唱出新的希望
'''.encode(),
    )
    context = attach_lyrics_reference({}, reference)
    segments = [
        _segment('song-1', 100_000, 101_000, '点亮心里的光'),
        _segment('song-2', 110_000, 111_000, '穿过黑夜和风浪'),
        _segment('song-3', 120_000, 121_000, '我们一起去远方'),
        _segment('biased-long-segment', 130_000, 137_000, '继续唱出新的希望'),
    ]

    result = apply_lyrics_correction_mode(segments, context, source_duration_ms=160_000)

    lyric_segments = [item for item in result.segments if item.get('lyricsCorrectionMode')]
    assert lyric_segments[-1]['text'] == '继续唱出新的希望'
    assert lyric_segments[-1]['startMs'] == 130_000
    assert result.report['alignment_anchor_count'] == 3
    assert result.report['applications'][0]['discarded_anchor_count'] == 1


def test_untimed_lyrics_use_asr_phonetic_anchors_and_restore_missing_line() -> None:
    reference = build_lyrics_reference(
        source_name='主题曲.txt',
        extracted_text='点亮心里的光\n穿过黑夜和风浪\n我们一起去远方',
    )
    context = attach_lyrics_reference({}, reference)
    segments = [
        _segment('dialogue-before', 0, 4_000, '现在开始表演'),
        _segment('song-1', 10_000, 12_000, '点亮心里的光'),
        _segment('song-wrong', 12_000, 14_000, '听不清楚的声音'),
        _segment('song-3', 14_000, 16_000, '我们一起去远方'),
        _segment('dialogue-after', 20_000, 23_000, '谢谢你们的表演'),
    ]

    result = apply_lyrics_correction_mode(segments, context, source_duration_ms=30_000)

    lyric_segments = [item for item in result.segments if item.get('lyricsCorrectionMode')]
    assert [item['text'] for item in lyric_segments] == [
        '点亮心里的光',
        '穿过黑夜和风浪',
        '我们一起去远方',
    ]
    assert all(item['timingEvidence'] == 'lyrics_auto_alignment' for item in lyric_segments)
    assert '现在开始表演' in [item['text'] for item in result.segments]
    assert '谢谢你们的表演' in [item['text'] for item in result.segments]
    assert result.report['alignment_anchor_count'] == 2


def test_disabled_mode_never_uses_stored_lyrics_reference() -> None:
    reference = build_lyrics_reference(
        source_name='主题曲.txt',
        extracted_text='点亮心里的光',
    )
    context = attach_lyrics_reference({}, reference)
    context['lyrics_correction_mode'] = False
    segments = [_segment('song-1', 10_000, 12_000, '点亮心中的光')]

    result = apply_lyrics_correction_mode(segments, context)

    assert result.segments == segments
    assert result.report['status'] == 'disabled'


def test_untimed_lyrics_without_reliable_anchor_keep_original_subtitles() -> None:
    reference = build_lyrics_reference(
        source_name='主题曲.txt',
        extracted_text='完全不同的歌词\n无法找到的位置',
    )
    context = attach_lyrics_reference({}, reference)
    segments = [_segment('dialogue', 0, 2_000, '欢迎来到节目')]

    result = apply_lyrics_correction_mode(segments, context)

    assert result.segments == segments
    assert result.report['status'] == 'not_applied'
    assert result.report['warnings']
