from app.services.asr_quality import sanitize_funasr_payload


UNIT = (
    '小时候我特别喜欢吃冰棍儿就算是在冬天也要和好朋友分享快乐时光'
    '还记得那年夏天我们一起奔跑在草坪中央梦想着未来永远不会散场'
)


def _words(text: str, *, confidence: float, offset: int) -> list[dict]:
    return [
        {
            'begin_time': offset + index * 100,
            'end_time': offset + (index + 1) * 100,
            'text': character,
            'punctuation': '',
            'confidence': confidence,
        }
        for index, character in enumerate(text)
    ]


def _payload(confidences: tuple[float, float, float]) -> dict:
    words = []
    offset = 10_000
    for confidence in confidences:
        repeated = _words(UNIT, confidence=confidence, offset=offset)
        words.extend(repeated)
        offset = repeated[-1]['end_time']
    return {
        'transcripts': [{
            'sentences': [{
                'sentence_id': 'song-1',
                'speaker_id': 1,
                'begin_time': words[0]['begin_time'],
                'end_time': words[-1]['end_time'],
                'text': UNIT * 3,
                'words': words,
            }],
        }],
    }


def test_sanitize_collapses_low_confidence_repetition_to_trusted_copy() -> None:
    result = sanitize_funasr_payload(_payload((0.00001, 0.97, 0.00001)))

    sentence = result.payload['transcripts'][0]['sentences'][0]
    assert sentence['text'] == UNIT
    assert len(sentence['words']) == len(UNIT)
    assert sentence['begin_time'] == 10_000 + len(UNIT) * 100
    assert result.report['anomaly_count'] == 1
    assert result.report['removed_word_count'] == len(UNIT) * 2
    assert result.report['anomalies'][0]['retained_median_confidence'] == 0.97


def test_sanitize_preserves_confident_repeated_chorus() -> None:
    payload = _payload((0.96, 0.97, 0.95))

    result = sanitize_funasr_payload(payload)

    assert result.payload == payload
    assert result.report['anomaly_count'] == 0
    assert result.report['removed_word_count'] == 0


def test_sanitize_removes_adjacent_low_confidence_tail_of_collapsed_repeat() -> None:
    payload = _payload((0.00001, 0.97, 0.00001))
    first = payload['transcripts'][0]['sentences'][0]
    tail_text = UNIT[-30:]
    tail_words = _words(
        tail_text,
        confidence=0.00001,
        offset=first['end_time'],
    )
    payload['transcripts'][0]['sentences'].append({
        'sentence_id': 'song-2',
        'speaker_id': 1,
        'begin_time': tail_words[0]['begin_time'],
        'end_time': tail_words[-1]['end_time'],
        'text': tail_text,
        'words': tail_words,
    })

    result = sanitize_funasr_payload(payload)

    assert len(result.payload['transcripts'][0]['sentences']) == 1
    assert result.report['anomaly_count'] == 2
    assert result.report['anomalies'][1]['type'] == 'low_confidence_trailing_repeat'
