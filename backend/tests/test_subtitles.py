from app.services.speakers import build_speaker_options
from app.services.subtitles import (
    apply_subtitle_speaker_overrides,
    apply_subtitle_text_overrides,
    build_script_text_correction_overrides,
    build_standalone_filler_delete_overrides,
    build_subtitle_cues,
    max_subtitle_chars_for_text,
    render_subtitle_content,
)


def test_build_subtitle_cues_applies_speaker_mapping() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 1000,
            'endMs': 2500,
            'text': '大家好，欢迎来到节目。',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '张三'})

    assert len(cues) == 1
    assert cues[0].speaker_name == '张三'
    assert cues[0].full_text == '张三：大家好欢迎来到节目'
    assert cues[0].clean_text == '大家好欢迎来到节目'


def test_build_subtitle_cues_applies_per_cue_speaker_override() -> None:
    segments = [
        {
            'speakerKey': 'speaker_6',
            'startMs': 0,
            'endMs': 2200,
            'text': '谢谢我想象不到',
            'subtitleParts': ['谢谢', '我想象不到'],
            'speakerOverrides': [{'partIndex': 0, 'speakerName': '杜海涛'}],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_6': '默认嘉宾'})

    assert [cue.speaker_name for cue in cues] == ['杜海涛', '默认嘉宾']
    assert cues[0].full_text == '杜海涛：谢谢'
    assert cues[1].full_text == '默认嘉宾：我想象不到'


def test_apply_subtitle_speaker_overrides_only_updates_target_cue() -> None:
    segments = [
        {
            'speakerKey': 'speaker_6',
            'startMs': 0,
            'endMs': 3000,
            'text': '谢谢我想象不到真的有点强',
            'subtitleParts': ['谢谢', '我想象不到', '真的有点强'],
        }
    ]

    updated = apply_subtitle_speaker_overrides(
        segments,
        [{'index': 2, 'speaker_name': '早安老师'}],
        {'speaker_6': '默认嘉宾'},
    )
    cues = build_subtitle_cues(updated, {'speaker_6': '默认嘉宾'})

    assert [cue.speaker_name for cue in cues] == ['默认嘉宾', '早安老师', '默认嘉宾']
    assert updated[0]['speakerOverrides'] == [{'partIndex': 1, 'speakerName': '早安老师'}]


def test_render_subtitle_content_supports_srt_and_clean_variant() -> None:
    segments = [
        {
            'speakerKey': 'speaker_1',
            'startMs': 0,
            'endMs': 1234,
            'text': '今天聊一下功能设计。',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_1': '李四'})
    content = render_subtitle_content(cues, subtitle_format='srt', variant='clean')

    assert '00:00:00,000 --> 00:00:01,234' in content
    assert '今天聊一下功能设计' in content
    assert '李四：' not in content
    assert '。' not in content


def test_build_subtitle_cues_splits_long_sentences_to_max_fourteen_chars() -> None:
    segments = [
        {
            'speakerKey': 'speaker_2',
            'startMs': 0,
            'endMs': 4200,
            'text': '这个功能现在已经可以自动识别说话人但是还需要继续优化字幕切分',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_2': '王五'})

    assert len(cues) >= 2
    assert all(len(cue.clean_text) <= 14 for cue in cues)
    assert ''.join(cue.clean_text for cue in cues) == '这个功能现在已经可以自动识别说话人但是还需要继续优化字幕切分'


def test_build_subtitle_cues_preserves_validated_v2_final_cues() -> None:
    segments = [
        {
            'speakerKey': 'speaker_1',
            'startMs': 6240,
            'endMs': 7400,
            'text': '我叫于伟学，',
            'v2FinalCue': True,
        },
        {
            'speakerKey': 'speaker_1',
            'startMs': 7400,
            'endMs': 10920,
            'text': '来自耕宇牧星北京空间科技技术有限公司。',
            'v2FinalCue': True,
        },
    ]

    cues = build_subtitle_cues(
        segments,
        {'speaker_1': '于伟学'},
        subtitle_length_mode='smart',
        subtitle_max_chars=14,
    )

    assert [cue.clean_text for cue in cues] == [
        '我叫于伟学',
        '来自耕宇牧星北京空间科技技术有限公司',
    ]
    assert [(cue.start_ms, cue.end_ms) for cue in cues] == [(6240, 7400), (7400, 10920)]
    clean_srt = render_subtitle_content(cues, subtitle_format='srt', variant='clean')
    assert '来自耕宇牧星北京空间科技技术有限公司' in clean_srt
    assert '\n来自\n\n' not in clean_srt


def test_build_subtitle_cues_normalizes_traditional_chinese_to_simplified() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 2000, 'text': '這裡就是瀋陽的國際賽艇中心哦'},
        {'speakerKey': 'speaker_0', 'startMs': 2200, 'endMs': 4200, 'text': '這個行業的觀察呀'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert ''.join(cue.clean_text for cue in cues) == '这里就是沈阳的国际赛艇中心哦这个行业的观察呀'
    assert all('這' not in cue.clean_text and '國' not in cue.clean_text and '觀' not in cue.clean_text for cue in cues)


def test_build_subtitle_cues_prefers_word_level_timestamps_when_available() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1800,
            'text': '投票结束，来吧。',
            'words': [
                {'startMs': 0, 'endMs': 700, 'text': '投票结束', 'punctuation': '，'},
                {'startMs': 1100, 'endMs': 1800, 'text': '来吧', 'punctuation': '。'},
            ],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 2
    assert cues[0].clean_text == '投票结束'
    assert cues[0].start_ms == 0
    assert cues[0].end_ms == 700
    assert cues[1].clean_text == '来吧'
    assert cues[1].start_ms == 1100
    assert cues[1].end_ms == 1800


def test_build_subtitle_cues_does_not_split_short_phrase_only_because_of_comma() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1800,
            'text': '那个，今天先这样。',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 1
    assert cues[0].clean_text == '那个今天先这样'


def test_build_subtitle_cues_with_word_timestamps_keeps_short_comma_pause_together() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1500,
            'text': '所以，先这样。',
            'words': [
                {'startMs': 0, 'endMs': 500, 'text': '所以', 'punctuation': '，'},
                {'startMs': 500, 'endMs': 1500, 'text': '先这样', 'punctuation': '。'},
            ],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 1
    assert cues[0].clean_text == '所以先这样'
    assert cues[0].start_ms == 0
    assert cues[0].end_ms == 1500


def test_build_subtitle_cues_splits_at_soft_break_when_phrase_is_long_enough() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 3200,
            'text': '我们先看一下数据，然后再继续讨论结论。',
            'words': [
                {'startMs': 0, 'endMs': 1200, 'text': '我们先看一下数据', 'punctuation': '，'},
                {'startMs': 1300, 'endMs': 3200, 'text': '然后再继续讨论结论', 'punctuation': '。'},
            ],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 2
    assert cues[0].clean_text == '我们先看一下数据'
    assert cues[1].clean_text == '然后再继续讨论结论'


def test_build_subtitle_cues_splits_paused_enumeration_markers() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 4200,
            'text': '原因有三第一生物材料与超声技术',
            'words': [
                {'startMs': 0, 'endMs': 900, 'text': '原因有三'},
                {'startMs': 1280, 'endMs': 1500, 'text': '第一'},
                {'startMs': 1880, 'endMs': 4200, 'text': '生物材料与超声技术'},
            ],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert [cue.clean_text for cue in cues] == ['原因有三', '第一', '生物材料与超声技术']


def test_build_subtitle_cues_rejects_override_crossing_paused_enumeration_markers() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 4200,
            'text': '原因有三第一生物材料与超声技术',
            'words': [
                {'startMs': 0, 'endMs': 900, 'text': '原因有三'},
                {'startMs': 1280, 'endMs': 1500, 'text': '第一'},
                {'startMs': 1880, 'endMs': 4200, 'text': '生物材料与超声技术'},
            ],
            'subtitleParts': ['原因有三第一', '生物材料与超声技术'],
            'subtitlePartRanges': [{'startMs': 0, 'endMs': 1500}, {'startMs': 1880, 'endMs': 4200}],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert [cue.clean_text for cue in cues] == ['原因有三', '第一', '生物材料与超声技术']


def test_build_subtitle_cues_keeps_ar_compound_together_when_splitting_plain_text() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2800,
            'text': '过往人们也会提AR眼镜但现在有了AI眼镜',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) >= 2
    assert all(len(cue.clean_text) <= 14 for cue in cues)
    assert ''.join(cue.clean_text for cue in cues) == '过往人们也会提AR眼镜但现在有了AI眼镜'
    assert any('AR眼镜' in cue.clean_text for cue in cues)
    assert any('AI眼镜' in cue.clean_text for cue in cues)


def test_build_subtitle_cues_keeps_ar_compound_together_with_word_timestamps() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2400,
            'text': '所以我非常赞同今天这位嘉宾说的AR眼镜',
            'words': [
                {'startMs': 0, 'endMs': 1800, 'text': '所以我非常赞同今天这位嘉宾说的AR', 'punctuation': ''},
                {'startMs': 1800, 'endMs': 2400, 'text': '眼镜', 'punctuation': '。'},
            ],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) >= 2
    assert all(len(cue.clean_text) <= 14 for cue in cues)
    assert ''.join(cue.clean_text for cue in cues) == '所以我非常赞同今天这位嘉宾说的AR眼镜'
    assert any('AR眼镜' in cue.clean_text for cue in cues)
    assert not any(
        cues[index].clean_text.endswith('AR') and cues[index + 1].clean_text.startswith('眼镜')
        for index in range(len(cues) - 1)
    )


def test_build_subtitle_cues_rebalances_boundary_to_keep_common_role_word() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2200,
            'text': '大家好我是深圳智映无碍科技创始人林志鹏',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 2
    assert ''.join(cue.clean_text for cue in cues) == '大家好我是深圳智映无碍科技创始人林志鹏'
    assert cues[0].clean_text == '大家好我是深圳智映无碍科技'
    assert cues[1].clean_text == '创始人林志鹏'
    assert not cues[1].clean_text.startswith('人')


def test_build_subtitle_cues_does_not_split_common_chinese_noun_phrase() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2400,
            'text': '谢谢各位刚才已经听到了智能眼镜的好处',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 2
    assert ''.join(cue.clean_text for cue in cues) == '谢谢各位刚才已经听到了智能眼镜的好处'
    assert cues[1].clean_text == '智能眼镜的好处'
    assert not any(
        cues[index].clean_text.endswith('眼') and cues[index + 1].clean_text.startswith('镜')
        for index in range(len(cues) - 1)
    )


def test_build_subtitle_cues_avoids_splitting_common_two_char_words() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 4200,
            'text': '你们觉得智能眼镜到底会不会成为未来世界我们人类的一个真正的入口',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    merged = ''.join(cue.clean_text for cue in cues)

    assert merged == '你们觉得智能眼镜到底会不会成为未来世界我们人类的一个真正的入口'
    assert all(len(cue.clean_text) <= 14 for cue in cues)
    assert '成为' in merged
    assert '真正的入口' in merged
    assert not any(
        (cues[index].clean_text.endswith('成') and cues[index + 1].clean_text.startswith('为'))
        or (cues[index].clean_text.endswith('真') and cues[index + 1].clean_text.startswith('正'))
        for index in range(len(cues) - 1)
    )


def test_build_subtitle_cues_prefers_break_before_pronoun_phrase() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2600,
            'text': '因为手机现在已经成为我们每一个人的器官',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 2
    assert ''.join(cue.clean_text for cue in cues) == '因为手机现在已经成为我们每一个人的器官'
    assert cues[0].clean_text == '因为手机现在已经成为'
    assert cues[1].clean_text == '我们每一个人的器官'
    assert not any(
        cues[index].clean_text.endswith('一') and cues[index + 1].clean_text.startswith('个')
        for index in range(len(cues) - 1)
    )


def test_build_subtitle_cues_does_not_split_phrase_like_youqu() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1800,
            'text': '我先给大家讲一个很有趣的观点啊',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert ''.join(cue.clean_text for cue in cues) == '我先给大家讲一个很有趣的观点啊'
    assert not any(
        cues[index].clean_text.endswith('有') and cues[index + 1].clean_text.startswith('趣')
        for index in range(len(cues) - 1)
    )


def test_build_subtitle_cues_does_not_split_phrase_like_zheyang() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1600,
            'text': '不知道大家身边有没有这样一群人',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert ''.join(cue.clean_text for cue in cues) == '不知道大家身边有没有这样一群人'
    assert not any(
        cues[index].clean_text.endswith('这') and cues[index + 1].clean_text.startswith('样')
        for index in range(len(cues) - 1)
    )


def test_build_subtitle_cues_prefers_segment_subtitle_parts_when_present() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2200,
            'text': '大家好欢迎来到节目今天继续聊字幕断句体验',
            'subtitleParts': ['大家好欢迎来到节目', '今天继续聊字幕断句体验'],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 2
    assert cues[0].clean_text == '大家好欢迎来到节目'
    assert cues[1].clean_text == '今天继续聊字幕断句体验'
    assert cues[0].start_ms == 0
    assert cues[1].end_ms == 2200


def test_build_subtitle_cues_supports_segment_subtitle_parts_with_different_count() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2200,
            'text': '大家好欢迎来到节目今天继续聊字幕断句体验',
            'subtitleParts': ['大家好欢迎来到节目', '今天继续聊', '字幕断句体验'],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 3
    assert [cue.clean_text for cue in cues] == ['大家好欢迎来到节目', '今天继续聊', '字幕断句体验']
    assert cues[0].start_ms == 0
    assert cues[-1].end_ms == 2200


def test_build_subtitle_cues_ignores_overlong_single_subtitle_part() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 2050,
            'endMs': 7610,
            'text': 'AGI bar其实就是一个没有bar就是没有门槛的一个行业的会场',
            'subtitleParts': ['AGI bar其实就是一个没有bar就是没有门槛的一个行业的会场'],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) > 1
    assert ''.join(cue.clean_text for cue in cues) == 'AGI bar其实就是一个没有bar就是没有门槛的一个行业的会场'
    assert all(len(cue.clean_text) <= max_subtitle_chars_for_text(cue.clean_text) for cue in cues)


def test_build_subtitle_cues_avoids_splitting_common_word_boundaries() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 8000,
            'endMs': 10500,
            'text': '包括很多我们的这个行业的观察呀',
            'subtitleParts': ['包括很多我们的这个行', '业的观察呀'],
        },
        {
            'speakerKey': 'speaker_0',
            'startMs': 14000,
            'endMs': 17000,
            'text': '这样非正式的会议当中慢慢地产生的',
            'subtitleParts': ['这样非正式的会议当中慢', '慢地产生的'],
        },
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '包括很多我们的这个行业的观察呀这样非正式的会议当中慢慢地产生的'
    assert not any(
        cue_texts[index].endswith('行') and cue_texts[index + 1].startswith('业')
        for index in range(len(cue_texts) - 1)
    )
    assert not any(
        cue_texts[index].endswith('慢') and cue_texts[index + 1].startswith('慢')
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_avoids_splitting_user_reported_compounds() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 58000,
            'endMs': 61000,
            'text': '大声说出你们给自己起的外号是什么',
            'subtitleParts': ['大声说出你们给自己起的外', '号是什么'],
        },
        {
            'speakerKey': 'speaker_1',
            'startMs': 62000,
            'endMs': 66000,
            'text': '呃大家好我是来自于北京人工智能的大表哥庞大为',
            'subtitleParts': ['呃大家好我是来自于北京人工', '智能的大表哥庞大为'],
        },
        {
            'speakerKey': 'speaker_2',
            'startMs': 84000,
            'endMs': 87000,
            'text': '大家好我是来自原力无限的刘洋啊',
            'subtitleParts': ['大家好我是来自原力无', '限的刘洋啊'],
        },
    ]

    cues = build_subtitle_cues(
        segments,
        {'speaker_0': '主持人', 'speaker_1': '嘉宾一', 'speaker_2': '嘉宾二'},
    )
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '大声说出你们给自己起的外号是什么呃大家好我是来自于北京人工智能的大表哥庞大为大家好我是来自原力无限的刘洋啊'
    assert all(len(cue.clean_text) <= max_subtitle_chars_for_text(cue.clean_text) for cue in cues)
    assert not any(
        (cue_texts[index].endswith('外') and cue_texts[index + 1].startswith('号'))
        or (cue_texts[index].endswith('人工') and cue_texts[index + 1].startswith('智能'))
        or (cue_texts[index].endswith('原力无') and cue_texts[index + 1].startswith('限'))
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_repairs_user_reported_compounds_across_segments() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 58000, 'endMs': 60000, 'text': '大声说出你们给自己起的外'},
        {'speakerKey': 'speaker_0', 'startMs': 60000, 'endMs': 62000, 'text': '号是什么'},
        {'speakerKey': 'speaker_1', 'startMs': 63000, 'endMs': 65000, 'text': '呃大家好我是来自于北京人工'},
        {'speakerKey': 'speaker_1', 'startMs': 65000, 'endMs': 67000, 'text': '智能的大表哥庞大为'},
        {'speakerKey': 'speaker_2', 'startMs': 84000, 'endMs': 85000, 'text': '大家好我是来自原力无'},
        {'speakerKey': 'speaker_2', 'startMs': 85000, 'endMs': 87500, 'text': '限的刘洋啊'},
    ]

    cues = build_subtitle_cues(
        segments,
        {'speaker_0': '主持人', 'speaker_1': '嘉宾一', 'speaker_2': '嘉宾二'},
    )
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '大声说出你们给自己起的外号是什么呃大家好我是来自于北京人工智能的大表哥庞大为大家好我是来自原力无限的刘洋啊'
    assert all(len(cue.clean_text) <= max_subtitle_chars_for_text(cue.clean_text) for cue in cues)
    assert not any(
        (cue_texts[index].endswith('外') and cue_texts[index + 1].startswith('号'))
        or (cue_texts[index].endswith('人工') and cue_texts[index + 1].startswith('智能'))
        or (cue_texts[index].endswith('原力无') and cue_texts[index + 1].startswith('限'))
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_avoids_splitting_corporate_names_and_common_words() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 35000,
            'endMs': 38000,
            'text': '下各位他们是北京元空智能科技有限公司创始人彭大为',
            'subtitleParts': ['下各位他们是北京元空智能科技', '有限公司创始人彭大为'],
        },
        {
            'speakerKey': 'speaker_0',
            'startMs': 40000,
            'endMs': 45000,
            'text': '还有元力无限智能科技杭州有限公司联合创始人刘洋',
            'subtitleParts': ['还有元力无限智能科技杭州有限', '公司联合创始人刘洋'],
        },
        {
            'speakerKey': 'speaker_2',
            'startMs': 101000,
            'endMs': 105000,
            'text': '其实是我们希望能做到用最聪明的大脑去做到最懂你的机器人',
            'subtitleParts': ['其实是我们希望能做到用最聪', '明的大脑去做到最懂你的机器人'],
        },
        {
            'speakerKey': 'speaker_1',
            'startMs': 232000,
            'endMs': 236000,
            'text': '但是我觉得这就是创业的魅力所在',
            'subtitleParts': ['但是我觉得这就是创', '业的魅力所在'],
        },
    ]

    cues = build_subtitle_cues(
        segments,
        {'speaker_0': '主持人', 'speaker_1': '嘉宾一', 'speaker_2': '嘉宾二'},
    )
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == (
        '下各位他们是北京元空智能科技有限公司创始人彭大为'
        '还有元力无限智能科技杭州有限公司联合创始人刘洋'
        '其实是我们希望能做到用最聪明的大脑去做到最懂你的机器人'
        '但是我觉得这就是创业的魅力所在'
    )
    assert not any(
        (cue_texts[index].endswith('有限公') and cue_texts[index + 1].startswith('司'))
        or (cue_texts[index].endswith('聪') and cue_texts[index + 1].startswith('明'))
        or (cue_texts[index].endswith('创') and cue_texts[index + 1].startswith('业'))
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_treats_fourteen_cjk_chars_as_soft_target_for_complete_phrases() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2800,
            'text': '刚刚我我毫不犹豫地投下了这一票',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert cue_texts == ['刚刚我我毫不犹豫地投下了这一票']
    assert len(cue_texts[0]) == 15
    assert '投下了这一票' in cue_texts[0]


def test_build_subtitle_cues_keeps_user_reported_phrase_patterns_whole() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 5200,
            'text': '这Peter这首歌是一首很感人的歌有亲人的人都会有这种感触',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '这Peter这首歌是一首很感人的歌有亲人的人都会有这种感触'
    assert any(text.endswith('很感人的歌') for text in cue_texts)
    assert cue_texts[-1].endswith('有这种感触')
    assert not any(
        (cue_texts[index].endswith('很') and cue_texts[index + 1].startswith('感人的歌'))
        or (cue_texts[index].endswith('很感人') and cue_texts[index + 1].startswith('的歌'))
        or (cue_texts[index].endswith('有这种') and cue_texts[index + 1].startswith('感触'))
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_still_splits_at_natural_boundary_under_soft_target() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 3600,
            'text': '我们先介绍人工智能然后一起学习一下',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert cue_texts == ['我们先介绍人工智能', '然后一起学习一下']
    assert all(len(text) <= max_subtitle_chars_for_text(text) for text in cue_texts)
    assert not any(
        (cue_texts[index].endswith('人工') and cue_texts[index + 1].startswith('智能'))
        or (cue_texts[index].endswith('学习') and cue_texts[index + 1].startswith('一下'))
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_does_not_keep_overlong_sentence_from_lexicon_whole() -> None:
    text = '社区学院看一看看他们是如何打通邻里之间'
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 5000,
            'text': text,
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'}, protected_terms=(text,))
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == text
    assert len(cue_texts) >= 2
    assert all(len(cue.clean_text) <= max_subtitle_chars_for_text(cue.clean_text) for cue in cues)


def test_build_subtitle_cues_prefers_break_after_kan_yi_kan_before_repeated_kan() -> None:
    text = '社区学院看一看看他们是如何打通邻里之间'
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 5000,
            'text': text,
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert cue_texts == ['社区学院看一看', '看他们是如何打通邻里之间']


def test_build_subtitle_cues_default_smart_mode_keeps_phrase_complete() -> None:
    text = '用户的付费能力一直是一个瓶颈'
    segments = [{
        'startMs': 0,
        'endMs': 3200,
        'speakerKey': 'speaker_0',
        'text': text,
    }]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert [cue.clean_text for cue in cues] == ['用户的付费能力一直是一个瓶颈']


def test_build_subtitle_cues_strict_mode_splits_overlong_cjk_at_safe_boundary() -> None:
    text = '社区学院看一看看他们是如何打通邻里之间'
    segments = [{
        'startMs': 0,
        'endMs': 4200,
        'speakerKey': 'speaker_0',
        'text': text,
    }]

    cues = build_subtitle_cues(
        segments,
        {'speaker_0': '主持人'},
        subtitle_length_mode='strict',
        subtitle_max_chars=10,
    )

    assert [cue.clean_text for cue in cues] == ['社区学院看一看', '看他们是如何打通', '邻里之间']
    assert all(len(cue.clean_text) <= 10 for cue in cues)


def test_build_subtitle_cues_strict_mode_keeps_protected_term_whole_when_over_limit() -> None:
    protected = '北京元空智能科技有限公司'
    text = f'大家好我是来自{protected}创始人'
    segments = [{
        'startMs': 0,
        'endMs': 4800,
        'speakerKey': 'speaker_0',
        'text': text,
    }]

    cues = build_subtitle_cues(
        segments,
        {'speaker_0': '主持人'},
        protected_terms=(protected,),
        subtitle_length_mode='strict',
        subtitle_max_chars=8,
    )

    joined = ''.join(cue.clean_text for cue in cues)
    assert joined == text
    assert any(cue.clean_text == protected for cue in cues)


def test_build_subtitle_cues_prefers_copula_boundary_before_future_solution_phrase() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 80,
            'endMs': 3560,
            'text': '我一直都非常认可机器人是未来解决养老的终极方案',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert cue_texts == ['我一直都非常认可', '机器人是', '未来解决养老的终极方案']
    assert not any(
        cue_texts[index].endswith('未来解决') and cue_texts[index + 1].startswith('养老')
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_rejects_override_that_strands_object_complement() -> None:
    text = '银发经济有一个问题是用户的付费能力一直是一个瓶颈'
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 3760,
            'endMs': 8520,
            'text': text,
            'subtitleParts': ['银发经济有一个问题是', '用户的付费能力一直是', '一个瓶颈'],
            'subtitlePartRanges': [
                {'startMs': 3760, 'endMs': 5040},
                {'startMs': 5778, 'endMs': 7467},
                {'startMs': 7467, 'endMs': 8520},
            ],
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == text
    assert cue_texts[:2] == ['银发经济有一个问题是', '用户的付费能力一直是一个瓶颈']
    assert not any(
        cue_texts[index].endswith('一直是') and cue_texts[index + 1].startswith('一个')
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_repairs_unsafe_boundaries_across_segments() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 8000, 'endMs': 9000, 'text': '包括很多我们的这个行'},
        {'speakerKey': 'speaker_0', 'startMs': 9000, 'endMs': 10500, 'text': '业的观察呀'},
        {'speakerKey': 'speaker_0', 'startMs': 14000, 'endMs': 15200, 'text': '这样非正式的会议当中慢'},
        {'speakerKey': 'speaker_0', 'startMs': 15200, 'endMs': 17000, 'text': '慢地产生的'},
        {'speakerKey': 'speaker_0', 'startMs': 18000, 'endMs': 19100, 'text': '也是东北平原上一颗璀'},
        {'speakerKey': 'speaker_0', 'startMs': 19100, 'endMs': 20500, 'text': '璨的绿色明珠'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '包括很多我们的这个行业的观察呀这样非正式的会议当中慢慢地产生的也是东北平原上一颗璀璨的绿色明珠'
    assert all(len(cue.clean_text) <= max_subtitle_chars_for_text(cue.clean_text) for cue in cues)
    assert not any(
        (cue_texts[index].endswith('行') and cue_texts[index + 1].startswith('业'))
        or (cue_texts[index].endswith('慢') and cue_texts[index + 1].startswith('慢'))
        or (cue_texts[index].endswith('璀') and cue_texts[index + 1].startswith('璨'))
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_reattaches_short_phrase_tail_from_wrong_speaker() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 19000, 'endMs': 22000, 'text': '这里就是沈阳的国际赛艇'},
        {'speakerKey': 'speaker_1', 'startMs': 22020, 'endMs': 22800, 'text': '中心哦'},
        {'speakerKey': 'speaker_1', 'startMs': 23000, 'endMs': 23800, 'text': '听说过'},
        {'speakerKey': 'speaker_0', 'startMs': 23800, 'endMs': 26000, 'text': '它是国内少有的集这个专业训练'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人', 'speaker_1': '嘉宾'})

    assert cues[0].speaker_key == 'speaker_0'
    assert cues[0].speaker_name == '主持人'
    assert cues[0].clean_text == '这里就是沈阳的国际赛艇中心哦'
    assert cues[0].start_ms == 19000
    assert cues[0].end_ms == 22800
    assert cues[1].speaker_key == 'speaker_1'
    assert cues[1].clean_text == '听说过'


def test_build_subtitle_cues_merges_isolated_single_cjk_before_export_validation() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 19000, 'endMs': 22000, 'text': '这里就是沈阳的国际赛艇中'},
        {'speakerKey': 'speaker_0', 'startMs': 22000, 'endMs': 22320, 'text': '心'},
        {'speakerKey': 'speaker_1', 'startMs': 23000, 'endMs': 23800, 'text': '听说过'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人', 'speaker_1': '嘉宾'})
    cue_texts = [cue.clean_text for cue in cues]

    assert cue_texts == ['这里就是沈阳的国际赛艇中心', '听说过']
    assert cues[0].speaker_key == 'speaker_0'
    assert cues[0].start_ms == 19000
    assert cues[0].end_ms == 22320


def test_build_subtitle_cues_preserves_worker_atom_across_segments() -> None:
    segments = [
        {'speakerKey': 'speaker_2', 'startMs': 36000, 'endMs': 38000, 'text': '你们好我是赛艇中心的工作'},
        {'speakerKey': 'speaker_2', 'startMs': 38000, 'endMs': 39200, 'text': '人员李丹'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_2': '李丹'})
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '你们好我是赛艇中心的工作人员李丹'
    assert any('工作人员' in cue.clean_text for cue in cues)
    assert not any(
        cue_texts[index].endswith('工作') and cue_texts[index + 1].startswith('人员')
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_preserves_shenyang_atom_across_segments() -> None:
    segments = [
        {'speakerKey': 'speaker_2', 'startMs': 48000, 'endMs': 50000, 'text': '是的像亚洲青年赛艇锦标赛和沈'},
        {'speakerKey': 'speaker_2', 'startMs': 50000, 'endMs': 52500, 'text': '阳赛艇国际公开赛呢'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_2': '李丹'})
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '是的像亚洲青年赛艇锦标赛和沈阳赛艇国际公开赛呢'
    assert not any(
        cue_texts[index].endswith('沈') and cue_texts[index + 1].startswith('阳')
        for index in range(len(cue_texts) - 1)
    )


def test_build_subtitle_cues_does_not_reattach_independent_short_reply() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 900, 'text': '你听说过吗'},
        {'speakerKey': 'speaker_1', 'startMs': 950, 'endMs': 1500, 'text': '听说过'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人', 'speaker_1': '嘉宾'})

    assert [cue.speaker_key for cue in cues] == ['speaker_0', 'speaker_1']
    assert [cue.clean_text for cue in cues] == ['你听说过吗', '听说过']


def test_build_subtitle_cues_removes_standalone_fillers_only_when_enabled() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 300, 'text': '嗯'},
        {'speakerKey': 'speaker_0', 'startMs': 500, 'endMs': 1200, 'text': '嗯这个可以'},
        {'speakerKey': 'speaker_0', 'startMs': 1400, 'endMs': 2100, 'text': '正式内容'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'}, remove_standalone_fillers=True)

    assert [cue.clean_text for cue in cues] == ['嗯这个可以', '正式内容']
    assert [cue.index for cue in cues] == [1, 2]


def test_build_subtitle_cues_keeps_standalone_fillers_by_default() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 300, 'text': '嗯'},
        {'speakerKey': 'speaker_0', 'startMs': 500, 'endMs': 1200, 'text': '正式内容'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert [cue.clean_text for cue in cues] == ['嗯', '正式内容']


def test_build_subtitle_cues_preserves_english_word_spaces() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 2000, 'text': 'I love hip hop every day'},
        {'speakerKey': 'speaker_0', 'startMs': 2100, 'endMs': 3000, 'text': '中国 新 说唱'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert cues[0].clean_text == 'I love hip hop every day'
    assert cues[1].clean_text == '中国新说唱'


def test_build_subtitle_cues_splits_long_english_on_word_boundaries() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 4000,
            'text': 'I got the power in my soul and we keep running through the night',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})

    assert len(cues) == 2
    assert [cue.clean_text for cue in cues] == [
        'I got the power in my soul and we keep',
        'running through the night',
    ]
    assert all(len(cue.clean_text) <= 42 for cue in cues)
    assert all('  ' not in cue.clean_text for cue in cues)


def test_build_subtitle_cues_strict_mode_splits_english_on_word_boundaries() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 3600,
            'text': 'Music make me one still shining',
        }
    ]

    cues = build_subtitle_cues(
        segments,
        {'speaker_0': '主持人'},
        subtitle_length_mode='strict',
        subtitle_max_chars=10,
    )

    assert [cue.clean_text for cue in cues] == ['Music make', 'me one', 'still', 'shining']
    assert all(len(cue.clean_text) <= 10 for cue in cues)


def test_build_subtitle_cues_keeps_single_long_english_word_whole() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2400,
            'text': 'Supercalifragilisticexpialidocious就是一个完整英文单词',
        }
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'})
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == 'Supercalifragilisticexpialidocious就是一个完整英文单词'
    assert cue_texts[0] == 'Supercalifragilisticexpialidocious'
    assert not any(
        'Supercalifragilisticexpialidocious'.startswith(cue_text)
        and cue_text != 'Supercalifragilisticexpialidocious'
        for cue_text in cue_texts
    )


def test_build_subtitle_cues_uses_script_terms_as_protected_atoms() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2600,
            'text': '我们现在一起进入蓝海数智体验馆参观',
        }
    ]
    script_context = {
        'terms': ['蓝海数智体验馆'],
        'reference_lines': ['我们现在一起进入蓝海数智体验馆参观'],
    }

    cues = build_subtitle_cues(segments, {'speaker_0': '主持人'}, script_context=script_context)
    cue_texts = [cue.clean_text for cue in cues]

    assert ''.join(cue_texts) == '我们现在一起进入蓝海数智体验馆参观'
    assert any('蓝海数智体验馆' in cue_text for cue_text in cue_texts)
    assert not any(
        cue_texts[index].endswith('体验') and cue_texts[index + 1].startswith('馆')
        for index in range(len(cue_texts) - 1)
    )


def test_apply_subtitle_text_overrides_deletes_standalone_filler_cue() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 300, 'text': '嗯嗯'},
        {'speakerKey': 'speaker_0', 'startMs': 500, 'endMs': 1200, 'text': '正式内容'},
    ]

    updated = apply_subtitle_text_overrides(segments, [{'index': 1, 'delete': True}], {'speaker_0': '主持人'})
    cues = build_subtitle_cues(updated, {'speaker_0': '主持人'})

    assert [cue.clean_text for cue in cues] == ['正式内容']
    assert cues[0].start_ms == 500


def test_build_standalone_filler_delete_overrides_targets_independent_and_edge_fillers() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 300, 'text': '嗯。'},
        {'speakerKey': 'speaker_0', 'startMs': 500, 'endMs': 2100, 'text': '啊'},
        {'speakerKey': 'speaker_0', 'startMs': 2300, 'endMs': 2900, 'text': '嗯这个问题我们看一下'},
        {'speakerKey': 'speaker_0', 'startMs': 3100, 'endMs': 3500, 'text': '哎呀'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '主持人'})

    assert overrides == [
        {'index': 1, 'delete': True},
        {'index': 2, 'delete': True},
        {'index': 3, 'clean_text': '这个问题我们看一下', 'start_ms': 2360},
        {'index': 4, 'delete': True},
    ]


def test_build_standalone_filler_delete_overrides_targets_wa_and_oh_edges() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 300, 'text': '哇'},
        {'speakerKey': 'speaker_1', 'startMs': 500, 'endMs': 800, 'text': '哦'},
        {'speakerKey': 'speaker_0', 'startMs': 1000, 'endMs': 1700, 'text': '红楼哇'},
        {'speakerKey': 'speaker_1', 'startMs': 1900, 'endMs': 2900, 'text': '向政府申请这个资金哦'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '主持人', 'speaker_1': '嘉宾'})

    assert overrides == [
        {'index': 1, 'delete': True},
        {'index': 2, 'delete': True},
        {'index': 3, 'clean_text': '红楼', 'end_ms': 1467},
        {'index': 4, 'clean_text': '向政府申请这个资金', 'end_ms': 2800},
    ]


def test_build_filler_delete_overrides_strips_edge_and_discourse_fillers() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1200, 'text': '哇来到户外真是空气清新'},
        {'speakerKey': 'speaker_0', 'startMs': 1300, 'endMs': 2200, 'text': '满眼苍翠啊'},
        {'speakerKey': 'speaker_0', 'startMs': 2300, 'endMs': 3500, 'text': '哎您会经常来户外做什么运动吗'},
        {'speakerKey': 'speaker_0', 'startMs': 3600, 'endMs': 5000, 'text': '其实呢以前我喜欢去香港'},
        {'speakerKey': 'speaker_0', 'startMs': 5100, 'endMs': 5700, 'text': '我觉得嘛'},
        {'speakerKey': 'speaker_0', 'startMs': 5800, 'endMs': 6200, 'text': '呢'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '主持人'})

    assert overrides == [
        {'index': 1, 'clean_text': '来到户外真是空气清新', 'start_ms': 109},
        {'index': 2, 'clean_text': '满眼苍翠', 'end_ms': 2020},
        {'index': 3, 'clean_text': '您会经常来户外做什么运动吗', 'start_ms': 2386},
        {'index': 4, 'clean_text': '以前我喜欢去香港', 'start_ms': 3982},
        {'index': 5, 'clean_text': '我觉得', 'end_ms': 5550},
        {'index': 6, 'delete': True},
    ]


def test_build_filler_delete_overrides_targets_repeated_particles_and_discourse_only_cues() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 2600, 'text': '嗯嗯嗯嗯'},
        {'speakerKey': 'speaker_0', 'startMs': 2800, 'endMs': 3600, 'text': '然后呢'},
        {'speakerKey': 'speaker_0', 'startMs': 3800, 'endMs': 4500, 'text': '对吧'},
        {'speakerKey': 'speaker_0', 'startMs': 4700, 'endMs': 6100, 'text': '其实呢以前我喜欢去香港'},
        {'speakerKey': 'speaker_0', 'startMs': 6300, 'endMs': 7600, 'text': '这个行业很重要'},
        {'speakerKey': 'speaker_0', 'startMs': 7800, 'endMs': 9100, 'text': '那个地方很美'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '主持人'})

    assert overrides == [
        {'index': 1, 'delete': True},
        {'index': 2, 'delete': True},
        {'index': 3, 'delete': True},
        {'index': 4, 'clean_text': '以前我喜欢去香港', 'start_ms': 5082},
    ]


def test_build_filler_delete_overrides_strips_embedded_er_before_discourse_filler() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1800, 'text': '2025年呃就是'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '主持人'})

    assert overrides == [
        {'index': 1, 'clean_text': '2025年就是'},
    ]


def test_build_filler_delete_overrides_strips_tail_confirmation_fillers() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1500, 'text': '我们可以这样做对吧'},
        {'speakerKey': 'speaker_0', 'startMs': 1800, 'endMs': 3200, 'text': '这个问题是不是'},
        {'speakerKey': 'speaker_0', 'startMs': 3500, 'endMs': 4300, 'text': '是不是'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '主持人'})

    assert overrides == [
        {'index': 1, 'clean_text': '我们可以这样做', 'end_ms': 1167},
        {'index': 2, 'clean_text': '这个问题', 'end_ms': 2600},
        {'index': 3, 'delete': True},
    ]


def test_apply_subtitle_text_overrides_can_trim_timing_when_stripping_fillers() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 1000,
            'endMs': 2200,
            'text': '满眼苍翠啊',
            'subtitleParts': ['满眼苍翠啊'],
            'subtitlePartRanges': [{'startMs': 1000, 'endMs': 2200}],
        }
    ]

    updated = apply_subtitle_text_overrides(
        segments,
        [{'index': 1, 'clean_text': '满眼苍翠', 'end_ms': 2050}],
        {'speaker_0': '主持人'},
    )
    cues = build_subtitle_cues(updated, {'speaker_0': '主持人'})

    assert [cue.clean_text for cue in cues] == ['满眼苍翠']
    assert cues[0].start_ms == 1000
    assert cues[0].end_ms == 2050


def test_delete_fillers_then_export_cues_close_short_gap_after_deleted_cue() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1000, 'text': '第一条'},
        {'speakerKey': 'speaker_1', 'startMs': 1100, 'endMs': 1200, 'text': '哦'},
        {'speakerKey': 'speaker_0', 'startMs': 1300, 'endMs': 2000, 'text': '第二条'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '甲', 'speaker_1': '乙'})
    updated = apply_subtitle_text_overrides(segments, overrides, {'speaker_0': '甲', 'speaker_1': '乙'})
    cues = build_subtitle_cues(updated, {'speaker_0': '甲', 'speaker_1': '乙'}, export_frame_rate='25')

    assert [cue.clean_text for cue in cues] == ['第一条', '第二条']
    assert cues[0].end_ms == 1300


def test_build_standalone_filler_delete_overrides_targets_short_laughter_only() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 500, 'text': '哈哈'},
        {'speakerKey': 'speaker_0', 'startMs': 800, 'endMs': 1300, 'text': '呵呵'},
        {'speakerKey': 'speaker_0', 'startMs': 1600, 'endMs': 3100, 'text': '哈哈'},
        {'speakerKey': 'speaker_0', 'startMs': 3300, 'endMs': 4100, 'text': '哈哈这个地方挺有趣'},
    ]

    overrides = build_standalone_filler_delete_overrides(segments, {'speaker_0': '主持人'})

    assert overrides == [
        {'index': 1, 'delete': True},
        {'index': 2, 'delete': True},
    ]


def test_apply_subtitle_text_overrides_edits_single_cue_and_keeps_timing() -> None:
    segments = [
        {
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2000,
            'text': '第一句第二句',
            'subtitleParts': ['第一句', '第二句'],
            'subtitlePartRanges': [{'startMs': 0, 'endMs': 800}, {'startMs': 900, 'endMs': 2000}],
        }
    ]

    updated = apply_subtitle_text_overrides(segments, [{'index': 2, 'clean_text': '第二句已修正'}], {'speaker_0': '主持人'})
    cues = build_subtitle_cues(updated, {'speaker_0': '主持人'})

    assert [cue.clean_text for cue in cues] == ['第一句', '第二句已修正']
    assert cues[1].start_ms == 900
    assert cues[1].end_ms == 2000


def test_build_script_text_correction_overrides_corrects_script_place_name() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1200, 'text': '扎记村现在留存下来的很多建筑'},
        {'speakerKey': 'speaker_0', 'startMs': 1400, 'endMs': 2200, 'text': '这个村很漂亮'},
    ]
    script_context = {
        'places': ['查济村'],
        'reference_lines': ['安徽省宣城市泾县查济村台本', '查济村现在留存下来的很多建筑'],
    }

    overrides = build_script_text_correction_overrides(segments, script_context, {'speaker_0': '主持人'})

    assert overrides == [
        {'index': 1, 'clean_text': '查济村现在留存下来的很多建筑'},
    ]


def test_build_subtitle_cues_closes_ten_frame_gap_at_25fps() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1000, 'text': '第一条'},
        {'speakerKey': 'speaker_1', 'startMs': 1390, 'endMs': 2000, 'text': '第二条'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '甲', 'speaker_1': '乙'}, export_frame_rate='25')

    assert cues[0].end_ms == 1390


def test_build_subtitle_cues_closes_ten_frame_gap_at_50fps_only_under_threshold() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1000, 'text': '第一条'},
        {'speakerKey': 'speaker_1', 'startMs': 1240, 'endMs': 2000, 'text': '第二条'},
    ]

    cues = build_subtitle_cues(segments, {'speaker_0': '甲', 'speaker_1': '乙'}, export_frame_rate='50')

    assert cues[0].end_ms == 1000


def test_build_speaker_options_counts_segments() -> None:
    segments = [
        {'speakerKey': 'speaker_0', 'text': 'A'},
        {'speakerKey': 'speaker_0', 'text': 'B'},
        {'speakerKey': 'speaker_1', 'text': 'C'},
    ]

    speakers = build_speaker_options(segments, {'speaker_0': '主持人'})

    assert speakers[0].speaker_key == 'speaker_0'
    assert speakers[0].speaker_name == '主持人'
    assert speakers[0].segment_count == 2
    assert speakers[1].segment_count == 1
