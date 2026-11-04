from __future__ import annotations

from app.services.qwen_filetrans import normalize_qwen_filetrans_payload


def test_normalize_qwen_filetrans_payload_reads_transcript_sentences() -> None:
    payload = {
        'transcripts': [
            {
                'sentences': [
                    {'begin_time': 0, 'end_time': 1000, 'text': '大家好'},
                    {'begin_time': 1000, 'end_time': 2200, 'text': '欢迎来到节目'},
                ]
            }
        ]
    }

    result = normalize_qwen_filetrans_payload(payload)

    assert result == [
        {'startMs': 0, 'endMs': 1000, 'text': '大家好'},
        {'startMs': 1000, 'endMs': 2200, 'text': '欢迎来到节目'},
    ]


def test_normalize_qwen_filetrans_payload_falls_back_to_paragraphs() -> None:
    payload = {
        'paragraphs': [
            {'begin_time': 0, 'end_time': 2000, 'text': '第一段'},
            {'begin_time': 2000, 'end_time': 3600, 'text': '第二段'},
        ]
    }

    result = normalize_qwen_filetrans_payload(payload)

    assert result[0]['text'] == '第一段'
    assert result[1]['endMs'] == 3600
