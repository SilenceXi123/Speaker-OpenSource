import sys
import types
from io import BytesIO
import zipfile

import pytest

from app.services.script_context import (
    extract_high_confidence_script_terms,
    extract_script_context,
    extract_script_document,
    extract_text_from_script_bytes,
    format_script_context_for_prompt,
    merge_script_contexts,
)


def test_extract_script_context_finds_named_terms() -> None:
    text = '中国说唱巅峰对决2026\n刘权 和 Rapeter吴嘉轩 在佛山古镇录制。AThree 提到 AI+ 制作流程。'

    context = extract_script_context(text, source_name='台本.txt')

    assert '刘权' in context['people']
    assert 'Rapeter吴嘉轩' in context['people']
    assert '佛山古镇' in context['places']
    assert '中国说唱巅峰对决2026' in context['programs']
    assert 'AI+' in context['terms']
    assert context['source_name'] == '台本.txt'
    assert context['summary']['keyword_count'] >= 5


def test_extract_script_context_prioritizes_ranked_people() -> None:
    context = extract_script_context(
        '\n'.join([
            '左近：第三位是——李铢衔 James。',
            '左近：第五位是——Miss Ko 葛仲珊。',
            '左近：第六位是——张峻豪。',
        ]),
        source_name='淘汰赛组队.pdf',
    )

    assert '李铢衔 James' in context['people']
    assert '李铢衔' in context['people']
    assert 'Miss Ko 葛仲珊' in context['people']
    assert '葛仲珊' in context['people']
    assert '张峻豪' in context['people']


def test_high_confidence_script_terms_bypass_capped_keyword_categories() -> None:
    context = {
        'people': [f'无关人名{index}' for index in range(40)],
        'reference_lines': [
            '左近：第三位是——李铢衔 James。',
            '左近：第五位是——Miss Ko 葛仲珊。',
            '有请首轮名人堂成员——艾热、rapeter、功夫胖。',
        ],
    }

    terms = extract_high_confidence_script_terms(context)

    assert {'李铢衔 James', '李铢衔', 'James'}.issubset(terms)
    assert {'Miss Ko 葛仲珊', 'Miss Ko', '葛仲珊'}.issubset(terms)
    assert {'艾热', 'rapeter', '功夫胖'}.issubset(terms)


def test_extract_text_from_srt_script_removes_timecodes() -> None:
    payload = '1\n00:00:01,000 --> 00:00:02,000\n刘权：你好\n'.encode()

    text = extract_text_from_script_bytes(payload, 'script.srt')

    assert '00:00:01' not in text
    assert '刘权' in text


def test_gold_srt_extracts_repeated_canonical_names_and_domain_terms() -> None:
    context = extract_script_context(
        '\n'.join([
            'SHarK米尔艾力选择了RANZER叶润泽',
            '欢迎SHarK米尔艾力',
            'RANZER叶润泽准备好了',
            'Rage（说唱风格）',
            'Beat switch（伴奏切换）',
            "Let's go（一起来）",
        ]),
        source_name='人工复核版.srt',
    )

    by_display = {item['display_text']: item for item in context['canonical_terms']}
    assert by_display['SHarK米尔艾力']['aliases'] == ['SHarK', '米尔艾力']
    assert by_display['RANZER叶润泽']['aliases'] == ['RANZER', '叶润泽']
    assert by_display['Rage']['term_type'] == 'term'
    assert by_display['Beat switch']['term_type'] == 'term'
    assert "Let's go" not in by_display
    assert context['gold_reference'] is True
    assert {'SHarK米尔艾力', 'RANZER叶润泽'}.issubset(
        extract_high_confidence_script_terms(context)
    )


def test_non_srt_does_not_promote_repeated_mixed_text_to_gold_canonical_term() -> None:
    context = extract_script_context(
        'SHarK米尔艾力\nSHarK米尔艾力',
        source_name='普通台本.txt',
    )

    assert context['canonical_terms'] == []
    assert context['gold_reference'] is False


def test_extract_text_from_pdf_script_uses_text_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pypdf = types.ModuleType('pypdf')

    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakePdfReader:
        def __init__(self, _stream) -> None:
            self.pages = [
                FakePage('红楼\n哇\n'),
                FakePage('向政府申请这个资金\n哦\n'),
            ]

    fake_pypdf.PdfReader = FakePdfReader
    monkeypatch.setitem(sys.modules, 'pypdf', fake_pypdf)

    text = extract_text_from_script_bytes(b'%PDF fake payload', '客户台本.pdf')

    assert '红楼' in text
    assert '向政府申请这个资金' in text
    assert '哇' in text
    assert '哦' in text


def test_extract_text_from_script_rejects_unknown_suffix_with_pdf_hint() -> None:
    with pytest.raises(ValueError, match='pdf'):
        extract_text_from_script_bytes(b'abc', '客户台本.xlsx')


def test_format_script_context_for_prompt_is_compact() -> None:
    context = {
        'people': ['刘权'],
        'places': ['佛山古镇'],
        'programs': ['中国说唱巅峰对决2026'],
        'organizations': [],
        'terms': ['AI+'],
        'reference_lines': ['I got the power in my soul', '中文唱词参考'],
    }

    prompt_text = format_script_context_for_prompt(context)

    assert '人名：刘权' in prompt_text
    assert '地名：佛山古镇' in prompt_text
    assert '专业词/易错词：AI+' in prompt_text
    assert '台本/唱词原文参考' in prompt_text
    assert 'I got the power in my soul' in prompt_text


def test_extract_script_context_keeps_reference_lines_for_lyrics() -> None:
    text = '中国新说唱\nI got the power in my soul\n中文唱词参考'

    context = extract_script_context(text, source_name='lyrics.txt')

    assert 'I got the power in my soul' in context['reference_lines']
    assert '中文唱词参考' in context['reference_lines']
    assert context['summary']['reference_line_count'] >= 2


def test_merge_script_contexts_keeps_both_sources() -> None:
    first = extract_script_context('SHARK 的作品是冰棍儿', source_name='第一份.pdf')
    second = extract_script_context('RENZER 的舞台很有 vibe', source_name='第二份.pdf')

    merged = merge_script_contexts(first, second)

    assert merged is not None
    assert merged['source_names'] == ['第一份.pdf', '第二份.pdf']
    assert merged['summary']['source_count'] == 2
    assert 'SHARK 的作品是冰棍儿' in merged['reference_lines']
    assert 'RENZER 的舞台很有 vibe' in merged['reference_lines']


def test_merge_script_contexts_keeps_first_case_and_spacing_variant() -> None:
    current = {
        'source_name': '本次台本.txt',
        'source_names': ['本次台本.txt'],
        'terms': ['ai', 'DeepSeek R1'],
        'reference_lines': ['欢迎使用 ai 创作。', 'DeepSeek R1 已经接入。'],
    }
    history = {
        'source_name': '历史台本.txt',
        'source_names': ['历史台本.txt'],
        'terms': ['AI', 'DeepSeekR1'],
        'reference_lines': ['欢迎使用 AI 创作。', 'DeepSeekR1 已经接入。'],
    }

    merged = merge_script_contexts(current, history)

    assert merged is not None
    assert merged['source_names'] == ['本次台本.txt', '历史台本.txt']
    assert [term for term in merged['terms'] if term.casefold() == 'ai'] == ['ai']
    assert merged['reference_lines'][:2] == [
        '欢迎使用 ai 创作。',
        'DeepSeek R1 已经接入。',
    ]
    assert '欢迎使用 AI 创作。' not in merged['reference_lines']
    assert 'DeepSeekR1 已经接入。' not in merged['reference_lines']


def test_merge_script_contexts_reverses_case_when_current_script_is_uppercase() -> None:
    current = {
        'source_name': '本次台本.txt',
        'source_names': ['本次台本.txt'],
        'terms': ['AI'],
        'reference_lines': ['欢迎使用 AI 创作。'],
    }
    history = {
        'source_name': '历史台本.txt',
        'source_names': ['历史台本.txt'],
        'terms': ['ai'],
        'reference_lines': ['欢迎使用 ai 创作。'],
    }

    merged = merge_script_contexts(current, history)

    assert merged is not None
    assert [term for term in merged['terms'] if term.casefold() == 'ai'] == ['AI']
    assert merged['reference_lines'] == ['欢迎使用 AI 创作。']


def test_merge_script_contexts_preserves_ordered_reference_source_groups() -> None:
    current = extract_script_context(
        '后面AI内容',
        source_name='本次台本.txt',
    )
    history = extract_script_context(
        '前面历史台本内容\n后面ai内容',
        source_name='历史台本.txt',
    )

    merged = merge_script_contexts(current, history)

    assert merged is not None
    assert [
        (
            source['source_name'],
            source['reference_lines'],
        )
        for source in merged['reference_sources']
    ] == [
        ('本次台本.txt', ['后面AI内容']),
        ('历史台本.txt', ['前面历史台本内容']),
    ]
    assert all(
        len(source['source_hash']) == 64
        for source in merged['reference_sources']
    )


def test_merge_script_contexts_does_not_drop_later_source_when_first_is_full() -> None:
    first = extract_script_context(
        '\n'.join(f'第一份台本第{index}行流程' for index in range(160)),
        source_name='第一份.pdf',
    )
    second = extract_script_context(
        '\n'.join(f'第二份台本第{index}行台词' for index in range(40)),
        source_name='第二份.pdf',
    )

    merged = merge_script_contexts(first, second)

    assert merged is not None
    assert '第二份台本第0行台词' in merged['reference_lines']
    assert '第二份台本第39行台词' in merged['reference_lines']
    assert merged['summary']['reference_line_count'] == 200


def test_extract_script_document_preserves_source_text_and_reports_source_kind() -> None:
    extracted = extract_script_document('臺灣台本\n第二行'.encode(), 'script.md')

    assert extracted.status == 'ready'
    assert extracted.source_kind == 'md'
    assert extracted.text == '臺灣台本\n第二行'
    assert extracted.warning is None


def test_extract_script_document_reads_docx_paragraphs_without_extra_dependency() -> None:
    payload = BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="urn:test"><w:body>'
            '<w:p><w:r><w:t>第一段</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>第二段</w:t></w:r></w:p>'
            '</w:body></w:document>',
        )

    extracted = extract_script_document(payload.getvalue(), 'script.docx')

    assert extracted.status == 'ready'
    assert extracted.source_kind == 'docx'
    assert extracted.text.splitlines() == ['第一段', '第二段']


def test_extract_script_document_marks_scanned_pdf_unextractable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pypdf = types.ModuleType('pypdf')

    class FakePage:
        def extract_text(self) -> str:
            return ''

    class FakePdfReader:
        def __init__(self, _stream) -> None:
            self.pages = [FakePage()]

    fake_pypdf.PdfReader = FakePdfReader
    monkeypatch.setitem(sys.modules, 'pypdf', fake_pypdf)

    extracted = extract_script_document(b'%PDF scanned', 'scan.pdf')

    assert extracted.status == 'unextractable'
    assert extracted.source_kind == 'pdf'
    assert extracted.text == ''
    assert '扫描' in str(extracted.warning)

    with pytest.raises(ValueError, match='扫描'):
        extract_text_from_script_bytes(b'%PDF scanned', 'scan.pdf')
