import base64
import json
import re
from pathlib import Path

import pytest

from app.schemas.subtitle import SubtitleCue
from app.services.premiere_xml import (
    build_premiere_subtitle_xml,
    build_premiere_xml,
    build_visual_aligned_subtitle_cues,
)
from app.services.subtitles import render_subtitle_content


def _extract_clipitem(content: str, clip_id: str) -> str:
    match = re.search(fr'<clipitem id="{re.escape(clip_id)}">.*?</clipitem>', content)
    assert match is not None
    return match.group(0)


def _extract_file(content: str, file_id: str) -> str:
    match = re.search(fr'<file id="{re.escape(file_id)}">.*?</file>', content)
    assert match is not None
    return match.group(0)


def _clip_value(clipitem: str, tag_name: str) -> str:
    match = re.search(fr'<{tag_name}>(.*?)</{tag_name}>', clipitem)
    assert match is not None
    return match.group(1)


def _assert_clip_has_source_handles(clipitem: str, file_el: str, *, frame_rate: int = 25) -> None:
    duration = int(_clip_value(clipitem, 'duration'))
    in_point = int(_clip_value(clipitem, 'in'))
    out_point = int(_clip_value(clipitem, 'out'))
    source_duration = int(_clip_value(file_el, 'duration'))
    ticks_per_frame = 12_700_800_000_000 // frame_rate

    assert in_point > 0
    assert out_point == in_point + duration
    assert source_duration > out_point
    assert _clip_value(clipitem, 'pproTicksIn') == str(in_point * ticks_per_frame)
    assert _clip_value(clipitem, 'pproTicksOut') == str(out_point * ticks_per_frame)


def test_build_premiere_subtitle_xml_is_media_free_and_preserves_short_gap() -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=1000, speaker_key='srt_1', speaker_name='', full_text='第一行', clean_text='第一行'),
        SubtitleCue(index=2, start_ms=1200, end_ms=2000, speaker_key='srt_2', speaker_name='', full_text='第二行', clean_text='第二行'),
    ]

    content = build_premiere_subtitle_xml('sample', cues, frame_rate='25')

    first = _extract_clipitem(content, 'clipitem-subtitle-1')
    second = _extract_clipitem(content, 'clipitem-subtitle-2')
    assert _clip_value(first, 'start') == '0'
    assert _clip_value(first, 'end') == '25'
    assert _clip_value(second, 'start') == '30'
    assert _clip_value(second, 'end') == '50'
    assert '<effectid>GraphicAndType</effectid>' in content
    assert 'clip-video-' not in content
    assert 'clip-audio-' not in content
    assert 'file-source-' not in content
    assert '<pathurl>' not in content


@pytest.mark.parametrize(
    (
        'frame_rate',
        'start_ms',
        'end_ms',
        'timebase',
        'ntsc',
        'expected_start',
        'expected_end',
        'ticks_per_frame',
    ),
    [
        ('25', 0, 10, '25', 'FALSE', 0, 1, 508_032_000_000),
        ('29.97', 1000, 2500, '30', 'TRUE', 30, 75, 423_783_360_000),
        ('50', 1040, 2080, '50', 'FALSE', 52, 104, 254_016_000_000),
    ],
)
def test_build_premiere_subtitle_xml_supports_all_cli_frame_rates(
    frame_rate: str,
    start_ms: int,
    end_ms: int,
    timebase: str,
    ntsc: str,
    expected_start: int,
    expected_end: int,
    ticks_per_frame: int,
) -> None:
    cues = [
        SubtitleCue(
            index=1,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker_key='srt_1',
            speaker_name='',
            full_text='字幕',
            clean_text='字幕',
        )
    ]

    content = build_premiere_subtitle_xml('frame-rate-check', cues, frame_rate=frame_rate)

    clipitem = _extract_clipitem(content, 'clipitem-subtitle-1')
    in_point = int(_clip_value(clipitem, 'in'))
    out_point = int(_clip_value(clipitem, 'out'))
    duration = int(_clip_value(clipitem, 'duration'))
    assert f'<rate><timebase>{timebase}</timebase><ntsc>{ntsc}</ntsc></rate>' in clipitem
    assert int(_clip_value(clipitem, 'start')) == expected_start
    assert int(_clip_value(clipitem, 'end')) == expected_end
    assert duration == expected_end - expected_start
    assert out_point - in_point == duration
    assert int(_clip_value(clipitem, 'pproTicksIn')) == in_point * ticks_per_frame
    assert int(_clip_value(clipitem, 'pproTicksOut')) == out_point * ticks_per_frame


def test_existing_premiere_xml_still_fills_short_visual_gap() -> None:
    cues = [
        SubtitleCue(index=1, start_ms=0, end_ms=1000, speaker_key='a', speaker_name='', full_text='一', clean_text='一'),
        SubtitleCue(index=2, start_ms=1200, end_ms=2000, speaker_key='b', speaker_name='', full_text='二', clean_text='二'),
    ]

    content = build_premiere_xml(
        job_id='legacy-gap',
        source_path='https://example.test/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        frame_rate='25',
        subtitle_mode='graphicandtype',
    )

    first = _extract_clipitem(content, 'clipitem-subtitle-1')
    assert _clip_value(first, 'end') == '30'


def test_build_premiere_xml_contains_media_and_overlays() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='撒贝宁',
            start_ms=0,
            end_ms=1800,
            full_text='撒贝宁：大家好',
            clean_text='大家好',
        )
    ]
    matched_assets = {
        '撒贝宁': {
            'speaker_name': '撒贝宁',
            'file_name': '撒贝宁.png',
            'public_url': 'https://example.com/撒贝宁.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-123',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='video',
    )

    assert '<!DOCTYPE xmeml>' in content
    assert '<xmeml version="4">' in content
    assert 'Speaker Export job-123' in content
    assert 'https://example.com/source.mp4' in content
    assert 'https://example.com/撒贝宁.png' in content
    assert '<marker>' not in content
    assert '<generatoritem' not in content
    assert '<clipitem id="clip-overlay-1">' in content
    assert '<sourcetrack><mediatype>video</mediatype><trackindex>1</trackindex></sourcetrack>' in content
    assert '<clipitem id="clip-audio-job-123">' in content
    assert content.count('<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>') == 1


def test_build_premiere_xml_supports_audio_only_jobs() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=2400,
            full_text='主持人：欢迎收听',
            clean_text='欢迎收听',
        )
    ]

    content = build_premiere_xml(
        job_id='job-audio',
        source_path='https://example.com/source.mp3',
        cues=cues,
        matched_assets={},
        source_media_kind='audio',
    )

    assert 'https://example.com/source.mp3' in content
    assert '<generatoritem' not in content
    assert '<marker>' not in content
    assert '<clipitem id="clip-audio-job-audio">' in content
    assert 'source-audio-audio' in content
    assert 'clip-video-job-audio' not in content


def test_build_premiere_xml_supports_audio_jobs_with_png_overlays() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=2400,
            full_text='主持人：欢迎收听',
            clean_text='欢迎收听',
        )
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-audio-png',
        source_path='https://example.com/source.mp3',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='audio',
    )

    assert 'https://example.com/主持人.png' in content
    assert '<clipitem id="clip-overlay-1">' in content
    assert '<sourcetrack><mediatype>video</mediatype><trackindex>1</trackindex></sourcetrack>' in content


def test_build_premiere_xml_lower_thirds_follow_each_subtitle_cue() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：欢迎收听',
            clean_text='欢迎收听',
        ),
        SubtitleCue(
            index=2,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=1200,
            end_ms=2000,
            full_text='主持人：继续介绍',
            clean_text='继续介绍',
        ),
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-overlay-cues',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='video',
    )

    overlay_1 = re.search(r'<clipitem id="clip-overlay-1">.*?</clipitem>', content).group(0)
    overlay_2 = re.search(r'<clipitem id="clip-overlay-2">.*?</clipitem>', content).group(0)

    assert content.count('clip-overlay-') == 2
    assert '<start>0</start>' in overlay_1
    assert '<end>30</end>' in overlay_1
    assert '<start>30</start>' in overlay_2
    assert '<end>50</end>' in overlay_2


def test_build_premiere_xml_supports_embedded_text_track_mode() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：欢迎大家',
            clean_text='欢迎大家',
        )
    ]

    content = build_premiere_xml(
        job_id='job-text-track',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        subtitle_mode='text_track',
    )

    assert '<generatoritem id="clip-subtitle-1">' in content
    assert '<name>Outline Text</name>' in content
    assert '<value>欢迎大家</value>' in content
    assert '<parameterid>textcolor</parameterid>' in content
    assert '<parameterid>linecolor</parameterid>' in content
    assert '<clipitem id="clip-video-job-text-track">' in content


def test_build_premiere_xml_supports_graphicandtype_mode() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：欢迎大家',
            clean_text='欢迎大家',
        )
    ]

    content = build_premiere_xml(
        job_id='job-graphic',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        subtitle_mode='graphicandtype',
    )

    assert '<effectid>GraphicAndType</effectid>' in content
    assert '<mediaSource>GraphicAndType</mediaSource>' in content
    assert '<sequence id="sequence-job-graphic" explodedTracks="true">' in content
    assert '<clipitem id="clipitem-subtitle-1">' in content
    assert '<name>欢迎大家</name>' in content
    assert '<name>Source Text</name>' in content
    assert '<name>位置</name>' in content
    assert '<name>缩放</name>' in content
    assert 'TL.SQTrackExpandedHeight="25"' in content

    source_text_match = re.search(r'<name>Source Text</name><hash>[^<]+</hash><value>([^<]+)</value>', content)
    assert source_text_match is not None

    raw = base64.b64decode(source_text_match.group(1))
    payload = json.loads(raw[8:].decode('utf-16le'))

    assert payload['mTextParam']['mStyleSheet']['mText'] == '欢迎大家'
    assert payload['mTextParam']['mStyleSheet']['mFontName']['mParamValues'][0][1] == 'PingFangTC-Regular'


def test_graphicandtype_clip_name_uses_subtitle_text() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：好 谢谢我们的说唱好友',
            clean_text='好 谢谢我们的说唱好友',
        )
    ]

    content = build_premiere_xml(
        job_id='job-graphic-name',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        subtitle_mode='graphicandtype',
    )

    subtitle = _extract_clipitem(content, 'clipitem-subtitle-1')

    assert _clip_value(subtitle, 'name') == '好 谢谢我们的说唱好友'
    assert '<name>图形</name>' not in subtitle


def test_build_premiere_xml_can_add_subtitle_backplate_track() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='尼格买提',
            start_ms=1950,
            end_ms=3190,
            full_text='尼格买提：做米果去了',
            clean_text='做米果去了',
        ),
        SubtitleCue(
            index=2,
            speaker_key='speaker_1',
            speaker_name='杜海涛',
            start_ms=3990,
            end_ms=6270,
            full_text='杜海涛：好 谢谢我们的说唱好友',
            clean_text='好 谢谢我们的说唱好友',
        ),
    ]
    backplate_asset = {
        'file_name': '唱词条底.png',
        'object_key': 'caption-backplate.png',
        'export_path': '/Volumes/ShowAssets/唱词条底.png',
    }

    content = build_premiere_xml(
        job_id='job-backplate',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        subtitle_mode='graphicandtype',
        subtitle_backplate_asset=backplate_asset,
        frame_rate=25,
    )

    subtitle_1 = _extract_clipitem(content, 'clipitem-subtitle-1')
    backplate_1 = _extract_clipitem(content, 'clip-backplate-1')
    backplate_file_1 = _extract_file(content, 'file-backplate-1')
    subtitle_2 = _extract_clipitem(content, 'clipitem-subtitle-2')
    backplate_2 = _extract_clipitem(content, 'clip-backplate-2')
    backplate_file_2 = _extract_file(content, 'file-backplate-2')

    assert content.count('clip-backplate-') == 2
    assert 'file:///Volumes/ShowAssets/%E5%94%B1%E8%AF%8D%E6%9D%A1%E5%BA%95.png' in content
    assert _clip_value(backplate_1, 'name') == '唱词条底.png'
    assert _clip_value(backplate_1, 'start') == _clip_value(subtitle_1, 'start')
    assert _clip_value(backplate_1, 'end') == _clip_value(subtitle_1, 'end')
    assert _clip_value(backplate_1, 'duration') == _clip_value(subtitle_1, 'duration')
    assert _clip_value(backplate_2, 'start') == _clip_value(subtitle_2, 'start')
    assert _clip_value(backplate_2, 'end') == _clip_value(subtitle_2, 'end')
    assert _clip_value(backplate_2, 'duration') == _clip_value(subtitle_2, 'duration')
    _assert_clip_has_source_handles(backplate_1, backplate_file_1)
    _assert_clip_has_source_handles(backplate_2, backplate_file_2)


def test_build_premiere_xml_chooses_backplate_by_nameplate_presence_not_file_name() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1200,
            full_text='主持人：有姓名时用上人名条底',
            clean_text='有姓名时用上人名条底',
        ),
        SubtitleCue(
            index=2,
            speaker_key='speaker_1',
            speaker_name='旁白',
            start_ms=1600,
            end_ms=2600,
            full_text='旁白：没有姓名时用纯唱词底',
            clean_text='没有姓名时用纯唱词底',
        ),
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'export_path': '/Volumes/ClientAssets/主持人.png',
        }
    }
    backplate_assets = {
        'with_speaker': {
            'file_name': '唱词底.png',
            'object_key': 'with-name.png',
            'export_path': '/Volumes/Shared/A/唱词底.png',
        },
        'without_speaker': {
            'file_name': '唱词底.png',
            'object_key': 'without-name.png',
            'export_path': '/Volumes/Shared/B/唱词底.png',
        },
    }

    content = build_premiere_xml(
        job_id='job-dual-backplate',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='video',
        subtitle_mode='graphicandtype',
        subtitle_backplate_asset=backplate_assets,
        frame_rate=25,
    )

    backplate_1 = _extract_clipitem(content, 'clip-backplate-1')
    backplate_2 = _extract_clipitem(content, 'clip-backplate-2')

    assert _clip_value(backplate_1, 'name') == '唱词底.png'
    assert _clip_value(backplate_2, 'name') == '唱词底.png'
    assert 'file:///Volumes/Shared/A/%E5%94%B1%E8%AF%8D%E5%BA%95.png' in backplate_1
    assert 'file:///Volumes/Shared/B/%E5%94%B1%E8%AF%8D%E5%BA%95.png' in backplate_2


def test_build_premiere_xml_does_not_borrow_explicit_backplate_from_other_role() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：有匹配人名条',
            clean_text='有匹配人名条',
        ),
        SubtitleCue(
            index=2,
            speaker_key='speaker_1',
            speaker_name='旁白',
            start_ms=1200,
            end_ms=2200,
            full_text='旁白：没有匹配人名条',
            clean_text='没有匹配人名条',
        ),
    ]

    content = build_premiere_xml(
        job_id='job-missing-role-backplate',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={
            '主持人': {
                'speaker_name': '主持人',
                'file_name': '主持人.png',
                'export_path': '/Volumes/ClientAssets/主持人.png',
            },
        },
        subtitle_mode='graphicandtype',
        subtitle_backplate_asset={
            'schema_version': 1,
            'roles': {
                'with_speaker': {
                    'file_name': '仅有人名条底.png',
                    'object_key': 'with-only.png',
                    'export_path': '/Volumes/Shared/仅有人名条底.png',
                },
            },
        },
    )

    assert content.count('clip-backplate-') == 1
    assert 'clip-backplate-1' in content
    assert 'clip-backplate-2' not in content


def test_graphicandtype_subtitle_clip_uses_same_edit_frames_as_lower_third() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=2060,
            end_ms=5120,
            full_text='主持人：这条字幕要和人名条同帧出现',
            clean_text='这条字幕要和人名条同帧出现',
        )
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-graphic-overlay-sync',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='video',
        subtitle_mode='graphicandtype',
        frame_rate=25,
    )

    subtitle = _extract_clipitem(content, 'clipitem-subtitle-1')
    lower_third = _extract_clipitem(content, 'clip-overlay-1')
    lower_third_file = _extract_file(content, 'file-overlay-1')

    assert _clip_value(subtitle, 'start') == _clip_value(lower_third, 'start') == '51'
    assert _clip_value(subtitle, 'end') == _clip_value(lower_third, 'end') == '128'
    assert _clip_value(subtitle, 'duration') == _clip_value(lower_third, 'duration') == '77'
    _assert_clip_has_source_handles(lower_third, lower_third_file)


def test_graphicandtype_subtitle_clip_has_source_handles_for_timeline_trimming() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=2060,
            end_ms=5120,
            full_text='主持人：图形字幕需要能向前修剪',
            clean_text='图形字幕需要能向前修剪',
        )
    ]

    content = build_premiere_xml(
        job_id='job-graphic-handles',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        subtitle_mode='graphicandtype',
        frame_rate=25,
    )

    subtitle = _extract_clipitem(content, 'clipitem-subtitle-1')
    subtitle_file = _extract_file(content, 'file-subtitle-1')
    duration = int(_clip_value(subtitle, 'duration'))
    in_point = int(_clip_value(subtitle, 'in'))
    out_point = int(_clip_value(subtitle, 'out'))
    source_duration = int(_clip_value(subtitle_file, 'duration'))
    ticks_per_frame = 12_700_800_000_000 // 25

    assert in_point > 0
    assert out_point == in_point + duration
    assert source_duration > out_point
    assert _clip_value(subtitle, 'pproTicksIn') == str(in_point * ticks_per_frame)
    assert _clip_value(subtitle, 'pproTicksOut') == str(out_point * ticks_per_frame)


def test_graphicandtype_static_parameters_use_local_zero_time_origin() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=2000,
            end_ms=3200,
            full_text='主持人：图形内部参数也要归零',
            clean_text='图形内部参数也要归零',
        )
    ]

    content = build_premiere_xml(
        job_id='job-graphic-zero-origin',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        subtitle_mode='graphicandtype',
        frame_rate=25,
    )

    subtitle = _extract_clipitem(content, 'clipitem-subtitle-1')

    assert '-91445760000000000' not in subtitle
    assert '<name>变换</name><IsTimeVarying>false</IsTimeVarying><ParameterControlType>11</ParameterControlType><LowerBound>false</LowerBound><UpperBound>false</UpperBound><value>0,false,0,0,0,0,0,0</value>' in subtitle
    assert '<name>位置</name><IsTimeVarying>false</IsTimeVarying><value>0,0.5:0.8796296296296297,0,0,0,0,0,0,5,4,0,0,0,0</value>' in subtitle
    assert '<name>缩放</name><IsTimeVarying>false</IsTimeVarying><ParameterControlType>2</ParameterControlType><LowerBound>0</LowerBound><UpperBound>400</UpperBound><value>0,100.,0,0,0,0,0,0</value>' in subtitle


def test_premiere_xml_quantizes_visual_cues_without_frame_expansion_overlap() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=39,
            end_ms=1001,
            full_text='主持人：第一句',
            clean_text='第一句',
        ),
        SubtitleCue(
            index=2,
            speaker_key='speaker_1',
            speaker_name='嘉宾',
            start_ms=1019,
            end_ms=1801,
            full_text='嘉宾：第二句',
            clean_text='第二句',
        ),
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
        },
        '嘉宾': {
            'speaker_name': '嘉宾',
            'file_name': '嘉宾.png',
            'public_url': 'https://example.com/嘉宾.png',
        },
    }

    content = build_premiere_xml(
        job_id='job-frame-grid',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='video',
        subtitle_mode='graphicandtype',
        frame_rate=25,
    )

    subtitle_1 = _extract_clipitem(content, 'clipitem-subtitle-1')
    subtitle_2 = _extract_clipitem(content, 'clipitem-subtitle-2')
    lower_third_1 = _extract_clipitem(content, 'clip-overlay-1')
    lower_third_2 = _extract_clipitem(content, 'clip-overlay-2')

    assert _clip_value(subtitle_1, 'start') == _clip_value(lower_third_1, 'start') == '1'
    assert _clip_value(subtitle_1, 'end') == _clip_value(lower_third_1, 'end') == '25'
    assert _clip_value(subtitle_2, 'start') == _clip_value(lower_third_2, 'start') == '25'
    assert _clip_value(subtitle_2, 'end') == _clip_value(lower_third_2, 'end') == '45'
    assert _clip_value(subtitle_1, 'end') == _clip_value(subtitle_2, 'start')
    assert _clip_value(lower_third_1, 'end') == _clip_value(lower_third_2, 'start')


def test_visual_aligned_srt_uses_same_frame_grid_as_xml_overlays() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=5180,
            end_ms=5390,
            full_text='主持人：欢迎大家',
            clean_text='欢迎大家',
        )
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-srt-frame-align',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='video',
        subtitle_mode='graphicandtype',
        frame_rate=25,
    )
    lower_third = _extract_clipitem(content, 'clip-overlay-1')
    aligned_cues = build_visual_aligned_subtitle_cues(cues, frame_rate=25)
    aligned_srt = render_subtitle_content(aligned_cues, subtitle_format='srt', variant='full')

    assert _clip_value(lower_third, 'start') == '129'
    assert _clip_value(lower_third, 'end') == '135'
    assert aligned_cues[0].start_ms == 5160
    assert aligned_cues[0].end_ms == 5400
    assert '00:00:05,160 --> 00:00:05,400' in aligned_srt


def test_build_premiere_xml_prefers_generator_template_when_available(tmp_path: Path) -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：欢迎大家',
            clean_text='欢迎大家',
        )
    ]
    template_path = tmp_path / 'premiere_subtitle_template.xml'
    template_path.write_text(
        '''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence>
    <media>
      <video>
        <track>
          <generatoritem id="template-generator">
            <name>Template Subtitle</name>
            <duration>25</duration>
            <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
            <start>0</start>
            <end>25</end>
            <in>0</in>
            <out>25</out>
            <enabled>TRUE</enabled>
            <effect>
              <name>Template Effect</name>
              <effectid>Template Effect</effectid>
              <effectcategory>Text</effectcategory>
              <effecttype>generator</effecttype>
              <mediatype>video</mediatype>
              <parameter>
                <parameterid>str</parameterid>
                <name>Text</name>
                <value>Template Text</value>
              </parameter>
            </effect>
          </generatoritem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>
''',
        encoding='utf-8',
    )

    content = build_premiere_xml(
        job_id='job-template',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        subtitle_mode='text_track',
        subtitle_template_path=str(template_path),
    )

    assert '<generatoritem id="clip-subtitle-1">' in content
    assert '<name>Template Effect</name>' in content
    assert '<value>欢迎大家</value>' in content
    assert 'Template Text' not in content


def test_build_premiere_xml_prefers_local_media_paths_when_available() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=2400,
            full_text='主持人：欢迎收听',
            clean_text='欢迎收听',
        )
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
            'local_path': '/Users/example/Desktop/assets/主持人.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-local',
        source_path='/Users/example/Desktop/media/source.mp3',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='audio',
    )

    assert 'file:///Users/example/Desktop/media/source.mp3' in content
    assert 'file:///Users/example/Desktop/assets/%E4%B8%BB%E6%8C%81%E4%BA%BA.png' in content
    assert 'https://example.com/主持人.png' not in content


def test_build_premiere_xml_prefers_export_path_over_local_path() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=2400,
            full_text='主持人：欢迎收听',
            clean_text='欢迎收听',
        )
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
            'local_path': '/Users/example/Desktop/assets/主持人.png',
            'export_path': '/Volumes/EditorAssets/主持人.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-export-path',
        source_path='/Users/example/Desktop/media/source.mp3',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='audio',
    )

    assert 'file:///Volumes/EditorAssets/%E4%B8%BB%E6%8C%81%E4%BA%BA.png' in content
    assert 'file:///Users/example/Desktop/assets/%E4%B8%BB%E6%8C%81%E4%BA%BA.png' not in content


def test_build_premiere_xml_supports_custom_frame_rate() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：欢迎',
            clean_text='欢迎',
        )
    ]

    content = build_premiere_xml(
        job_id='job-fps',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets={},
        source_media_kind='video',
        frame_rate=50,
    )

    assert '<timebase>50</timebase>' in content


def test_build_premiere_xml_uses_fractional_ntsc_frame_rate_for_shared_visual_spans() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=1000,
            end_ms=2500,
            full_text='主持人：欢迎来到节目',
            clean_text='欢迎来到节目',
        )
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
            'public_url': 'https://example.com/主持人.png',
        }
    }

    content = build_premiere_xml(
        job_id='job-ntsc',
        source_path='https://example.com/source.mp4',
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind='video',
        frame_rate='29.97',
        subtitle_mode='graphicandtype',
    )

    assert '<timebase>30</timebase><ntsc>TRUE</ntsc>' in content
    subtitle_clip = _extract_clipitem(content, 'clipitem-subtitle-1')
    overlay_clip = _extract_clipitem(content, 'clip-overlay-1')
    assert _clip_value(subtitle_clip, 'start') == '30'
    assert _clip_value(subtitle_clip, 'end') == '75'
    assert _clip_value(subtitle_clip, 'start') == _clip_value(overlay_clip, 'start')
    assert _clip_value(subtitle_clip, 'end') == _clip_value(overlay_clip, 'end')


def test_build_premiere_xml_rejects_overlay_without_any_available_reference() -> None:
    cues = [
        SubtitleCue(
            index=1,
            speaker_key='speaker_0',
            speaker_name='主持人',
            start_ms=0,
            end_ms=1000,
            full_text='主持人：欢迎',
            clean_text='欢迎',
        )
    ]
    matched_assets = {
        '主持人': {
            'speaker_name': '主持人',
            'file_name': '主持人.png',
        }
    }

    with pytest.raises(Exception) as error:
        build_premiere_xml(
            job_id='job-missing-asset-path',
            source_path='https://example.com/source.mp4',
            cues=cues,
            matched_assets=matched_assets,
            source_media_kind='video',
        )

    assert '人名条缺少可用于 XML 导出的路径' in str(error.value)
