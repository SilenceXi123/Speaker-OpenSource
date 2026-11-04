from xml.etree.ElementTree import fromstring

import pytest

from app.services.subtitle_interchange import (
    SrtConversionError,
    convert_srt_bytes_to_premiere_xml,
    decode_srt_bytes,
    parse_srt_text,
)


def test_parse_srt_preserves_multiline_text_and_exact_milliseconds() -> None:
    cues = parse_srt_text(
        '1\r\n00:00:01,040 --> 00:00:02,080\r\n主持人：AI & 创作\r\n第二行\r\n\r\n'
        '2\r\n00:00:02.400 --> 00:00:03.000\r\n<保持原样>\r\n'
    )

    assert [(cue.index, cue.start_ms, cue.end_ms, cue.clean_text) for cue in cues] == [
        (1, 1040, 2080, '主持人：AI & 创作\n第二行'),
        (2, 2400, 3000, '<保持原样>'),
    ]
    assert cues[0].full_text == cues[0].clean_text
    assert cues[0].speaker_name == ''


def test_parse_srt_removes_only_leading_bom_and_preserves_in_body_bom() -> None:
    cues = parse_srt_text('\ufeff1\n00:00:00,000 --> 00:00:01,000\nA\ufeffB')

    assert cues[0].clean_text == 'A\ufeffB'


def test_decode_srt_supports_utf8_bom_and_gb18030() -> None:
    assert decode_srt_bytes('\ufeff中文字幕'.encode('utf-8')) == '中文字幕'
    assert decode_srt_bytes('中文 GB 字幕'.encode('gb18030')) == '中文 GB 字幕'


def test_convert_srt_builds_parseable_media_free_xml_without_gap_fill() -> None:
    payload = (
        '1\n00:00:00,000 --> 00:00:01,000\n第一条\n\n'
        '2\n00:00:01,200 --> 00:00:02,000\n第二条\n'
    ).encode()

    result = convert_srt_bytes_to_premiere_xml(
        payload,
        source_name='测试字幕.srt',
        frame_rate='25',
    )

    assert result.cue_count == 2
    assert result.duration_ms == 2000
    assert result.frame_rate == '25'
    assert '<end>25</end>' in result.xml_content
    assert '<start>30</start>' in result.xml_content
    assert '<pathurl>' not in result.xml_content
    fromstring(result.xml_content.split('<!DOCTYPE xmeml>\n', 1)[1])


def test_convert_srt_preserves_xml_valid_special_characters_and_emoji() -> None:
    cue_text = 'A\ufeffB & <保持原样> 😀'
    payload = f'1\n00:00:00,000 --> 00:00:01,000\n{cue_text}\n'.encode()

    result = convert_srt_bytes_to_premiere_xml(
        payload,
        source_name='节目 & <终版> 😀.srt',
        frame_rate='25',
    )

    root = fromstring(result.xml_content.split('<!DOCTYPE xmeml>\n', 1)[1])
    assert cue_text in list(root.itertext())
    assert root.findtext('./sequence/name') == '节目 & <终版> 😀 字幕'


def test_convert_srt_rejects_xml_invalid_cue_character_with_exact_location() -> None:
    payload = '1\n00:00:00,000 --> 00:00:01,000\n安全行\n机密\x00字幕\n'.encode()

    with pytest.raises(SrtConversionError) as captured:
        convert_srt_bytes_to_premiere_xml(
            payload,
            source_name='测试字幕.srt',
            frame_rate='25',
        )

    assert captured.value.code == 'invalid_srt'
    assert captured.value.details == {
        'cue_number': 1,
        'line_number': 4,
        'code_point': 'U+0000',
    }
    assert '机密' not in captured.value.message
    assert '机密' not in str(captured.value.details)


@pytest.mark.parametrize(
    ('character', 'code_point'),
    [
        ('\x08', 'U+0008'),
        ('\ud800', 'U+D800'),
        ('\ufffe', 'U+FFFE'),
        ('\uffff', 'U+FFFF'),
    ],
)
def test_parse_srt_rejects_other_xml_invalid_character_classes(
    character: str,
    code_point: str,
) -> None:
    with pytest.raises(SrtConversionError) as captured:
        parse_srt_text(f'1\n00:00:00,000 --> 00:00:01,000\nA{character}B')

    assert captured.value.code == 'invalid_srt'
    assert captured.value.details == {
        'cue_number': 1,
        'line_number': 3,
        'code_point': code_point,
    }


def test_convert_srt_rejects_xml_invalid_derived_sequence_name() -> None:
    payload = '1\n00:00:00,000 --> 00:00:01,000\n字幕\n'.encode()

    with pytest.raises(SrtConversionError) as captured:
        convert_srt_bytes_to_premiere_xml(
            payload,
            source_name='项目机密\x00终版.srt',
            frame_rate='25',
        )

    assert captured.value.code == 'invalid_srt'
    assert captured.value.details == {
        'field': 'source_name',
        'code_point': 'U+0000',
    }
    assert '项目机密' not in captured.value.message
    assert '项目机密' not in str(captured.value.details)


def test_convert_srt_rejects_post_frame_snap_collision_on_later_cue() -> None:
    payload = (
        '1\n00:00:00,000 --> 00:00:00,010\n第一条\n\n'
        '2\n00:00:00,020 --> 00:00:00,030\n第二条\n'
    ).encode()

    with pytest.raises(SrtConversionError) as captured:
        convert_srt_bytes_to_premiere_xml(
            payload,
            source_name='测试字幕.srt',
            frame_rate='25',
        )

    assert captured.value.code == 'invalid_srt'
    assert captured.value.details == {
        'cue_number': 2,
        'previous_cue_number': 1,
        'frame_rate': '25',
        'start_frame': 0,
        'previous_end_frame': 1,
    }


@pytest.mark.parametrize(
    ('text', 'code'),
    [
        ('', 'invalid_srt'),
        ('1\nnot-a-time\n字幕', 'invalid_srt'),
        ('1\n00:00:01,000 --> 00:00:01,000\n字幕', 'invalid_srt'),
        ('1\n00:00:02,000 --> 00:00:03,000\n后面\n\n2\n00:00:01,000 --> 00:00:01,500\n前面', 'invalid_srt'),
        ('1\n00:00:00,000 --> 00:00:02,000\n一\n\n2\n00:00:01,900 --> 00:00:03,000\n二', 'overlapping_cues'),
    ],
)
def test_parse_srt_rejects_invalid_timeline_as_one_failure(text: str, code: str) -> None:
    with pytest.raises(SrtConversionError) as captured:
        parse_srt_text(text)
    assert captured.value.code == code
    assert captured.value.details.get('cue_number') or captured.value.details.get('line_number')


def test_decode_srt_rejects_unknown_encoding() -> None:
    with pytest.raises(SrtConversionError) as captured:
        decode_srt_bytes(b'\xff')
    assert captured.value.code == 'unsupported_srt_encoding'
