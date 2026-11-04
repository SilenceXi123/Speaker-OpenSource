from app.services.normalize import merge_funasr_payloads, normalize_funasr_payload


def test_normalize_funasr_payload_returns_segments() -> None:
    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'begin_time': 100,
                        'end_time': 900,
                        'text': '你好，世界。',
                        'speaker_id': 2,
                        'words': [
                            {'begin_time': 100, 'end_time': 300, 'text': '你好', 'punctuation': '，'},
                            {'begin_time': 300, 'end_time': 900, 'text': '世界', 'punctuation': '。'},
                        ],
                    }
                ]
            }
        ]
    }

    result = normalize_funasr_payload(payload)

    assert len(result) == 1
    assert result[0]['speakerKey'] == 'speaker_2'
    assert result[0]['startMs'] == 100
    assert result[0]['endMs'] == 900
    assert result[0]['words'][1]['punctuation'] == '。'


def test_merge_funasr_payloads_drops_exact_duplicates_in_overlap() -> None:
    chunk0 = {
        'transcripts': [
            {
                'sentences': [
                    {'sentence_id': 1, 'begin_time': 0, 'end_time': 5000, 'text': '你好世界', 'speaker_id': 0, 'words': []},
                    {'sentence_id': 2, 'begin_time': 7150000, 'end_time': 7195000, 'text': '这句话在重叠窗口', 'speaker_id': 1, 'words': []},
                ]
            }
        ]
    }
    chunk1 = {
        'transcripts': [
            {
                'sentences': [
                    {'sentence_id': 1, 'begin_time': 0, 'end_time': 4500, 'text': '这句话在重叠窗口', 'speaker_id': 1, 'words': []},
                    {'sentence_id': 2, 'begin_time': 5000, 'end_time': 9000, 'text': '重叠后的新内容', 'speaker_id': 0, 'words': []},
                ]
            }
        ]
    }

    merged = merge_funasr_payloads([(chunk0, 0, 0), (chunk1, 7200000, 1000)])

    sentences = merged['transcripts'][0]['sentences']
    assert [sentence['text'] for sentence in sentences].count('这句话在重叠窗口') == 1
    assert len(sentences) == 3
    kept = next(sentence for sentence in sentences if sentence['text'] == '这句话在重叠窗口')
    assert kept['begin_time'] == 7150000  # 保留前一片的版本


def test_merge_funasr_payloads_drops_overlap_region_from_previous_chunk() -> None:
    # 前片整句落在重叠区（起点在边界之后）→ 丢弃前片版本，保留后片完整版本
    chunk0 = {
        'transcripts': [
                    {
                        'sentences': [
                            {'sentence_id': 1, 'begin_time': 15000, 'end_time': 21920, 'text': '这是过去一年我们最重要的经验。', 'speaker_id': 0, 'words': []},
                            {'sentence_id': 2, 'begin_time': 27390, 'end_time': 32590, 'text': '大家好，接下来我想分享另一个案例，在这个案例里，我们用。', 'speaker_id': 0, 'words': [
                                {'begin_time': 27390, 'end_time': 32590, 'text': '大家好，接下来我想分享另一个案例，在这个案例里，我们用。', 'punctuation': ''},
                            ]},
                        ]
                    }
                ]
    }
    chunk1 = {
        'transcripts': [
            {
                'sentences': [
                    {'sentence_id': 1, 'begin_time': 2800, 'end_time': 6100, 'text': '大家好，接下来我想分享另一个案例。', 'speaker_id': 0, 'words': [
                        {'begin_time': 2800, 'end_time': 6100, 'text': '大家好，接下来我想分享另一个案例。', 'punctuation': ''},
                    ]},
                    {'sentence_id': 2, 'begin_time': 6500, 'end_time': 16300, 'text': '在这个案例里，我们遇到了时间轴对齐的挑战，最终解决了问题。', 'speaker_id': 0, 'words': [
                        {'begin_time': 6500, 'end_time': 16300, 'text': '在这个案例里，我们遇到了时间轴对齐的挑战，最终解决了问题。', 'punctuation': ''},
                    ]},
                ]
            }
        ]
    }

    merged = merge_funasr_payloads([(chunk0, 0, 0), (chunk1, 24000, 1000)])

    texts = [sentence['text'] for sentence in merged['transcripts'][0]['sentences']]
    assert texts == [
        '这是过去一年我们最重要的经验。',
        '大家好，接下来我想分享另一个案例。',
        '在这个案例里，我们遇到了时间轴对齐的挑战，最终解决了问题。',
    ]


def test_merge_funasr_payloads_word_boundary_split_is_monotonic() -> None:
    # 跨边界句子：前片只保留边界前词（文本重建），后片只保留边界后词（丢弃抖动词），词流单调
    chunk0 = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'begin_time': 23500,
                        'end_time': 24500,
                        'text': '呃那肯定',
                        'speaker_id': 0,
                        'words': [
                            {'begin_time': 23500, 'end_time': 23800, 'text': '呃', 'punctuation': ''},
                            {'begin_time': 23900, 'end_time': 24100, 'text': '那', 'punctuation': ''},
                            {'begin_time': 24200, 'end_time': 24500, 'text': '肯定', 'punctuation': ''},
                        ],
                    },
                ]
            }
        ]
    }
    chunk1 = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'begin_time': 0,
                        'end_time': 700,
                        'text': '那肯定',
                        'speaker_id': 0,
                        'words': [
                            {'begin_time': -50, 'end_time': 250, 'text': '那', 'punctuation': ''},
                            {'begin_time': 0, 'end_time': 300, 'text': '那', 'punctuation': ''},
                            {'begin_time': 400, 'end_time': 700, 'text': '肯定', 'punctuation': ''},
                        ],
                    },
                ]
            }
        ]
    }

    merged = merge_funasr_payloads([(chunk0, 0, 0), (chunk1, 24000, 1000)])

    sentences = merged['transcripts'][0]['sentences']
    words = [(word['begin_time'], word['end_time'], word['text']) for sentence in sentences for word in sentence['words']]
    assert all(words[i + 1][0] >= words[i][1] for i in range(len(words) - 1))  # 词流单调
    assert words == [(23500, 23800, '呃'), (24000, 24300, '那'), (24400, 24700, '肯定')]
    assert sentences[0]['text'] == '呃'  # 前片文本从保留词重建
