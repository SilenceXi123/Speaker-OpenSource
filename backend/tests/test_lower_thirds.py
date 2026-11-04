from app.services.lower_thirds import build_lower_third_asset, match_lower_thirds
from app.services.render import _build_overlay_filter, build_overlay_intervals, resolve_asset_media_path
from app.schemas.subtitle import SubtitleCue


def test_match_lower_thirds_by_file_stem() -> None:
    asset = build_lower_third_asset(
        file_name='主持人.png',
        object_key='assets/lower-thirds/主持人.png',
        public_url='https://example.com/主持人.png',
    )

    matched, missing = match_lower_thirds(['主持人'], [asset])

    assert '主持人' in matched
    assert missing == []


def test_build_overlay_intervals_follow_each_subtitle_cue() -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=1000, speaker_key='speaker_0', speaker_name='张三', full_text='张三：你好', clean_text='你好'),
        SubtitleCue(index=2, start_ms=1200, end_ms=2000, speaker_key='speaker_0', speaker_name='张三', full_text='张三：继续', clean_text='继续'),
        SubtitleCue(index=3, start_ms=3000, end_ms=3800, speaker_key='speaker_1', speaker_name='李四', full_text='李四：切换', clean_text='切换'),
    ]

    intervals = build_overlay_intervals(cues)

    assert len(intervals) == 3
    assert intervals[0]['speaker_name'] == '张三'
    assert intervals[0]['start'] == 0.0
    assert intervals[0]['end'] == 1.0
    assert intervals[1]['speaker_name'] == '张三'
    assert intervals[1]['start'] == 1.2
    assert intervals[1]['end'] == 2.0
    assert intervals[2]['speaker_name'] == '李四'


def test_build_overlay_filter_stops_with_main_video() -> None:
    interval = {'speaker_name': '张三', 'start': 0.6, 'end': 3.52}

    result = _build_overlay_filter('[0:v]', 1, interval, '[v1]')

    assert 'shortest=1' in result
    assert "between(t,0.600,3.520)" in result


def test_resolve_asset_media_path_prefers_local_path(tmp_path) -> None:
    local_asset = tmp_path / '主持人.png'
    local_asset.write_bytes(b'png')

    result = resolve_asset_media_path(
        {
            'file_name': '主持人.png',
            'local_path': str(local_asset),
        },
        tmp_path,
    )

    assert result == local_asset
