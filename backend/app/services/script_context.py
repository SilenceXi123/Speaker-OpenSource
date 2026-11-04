from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import re
from html import unescape
from pathlib import Path
from typing import Any, Optional
import zipfile

from app.services.chinese_text import normalize_to_simplified_chinese


MAX_TEXT_CHARS = 80_000
MAX_ITEMS_PER_CATEGORY = 40
MAX_REFERENCE_LINES = 160
MAX_GOLD_SRT_REFERENCE_LINES = 2_000
MAX_MERGED_REFERENCE_LINES = 640
MAX_REFERENCE_TEXT_CHARS = 6000
REFERENCE_LINE_MAX_CHARS = 160
TIME_CODE_RE = re.compile(r'^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s+-->\s+\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}')
SRT_INDEX_RE = re.compile(r'^\s*\d+\s*$')
TAG_RE = re.compile(r'<[^>]+>')
TITLE_RE = re.compile(r'[《「“"]([^》」”"]{2,30})[》」”"]')
LATIN_MIXED_RE = re.compile(r'[A-Za-z][A-Za-z0-9+\-_.]*[\u4e00-\u9fff]{1,6}|[\u4e00-\u9fff]{1,6}[A-Za-z][A-Za-z0-9+\-_.]*|[A-Za-z][A-Za-z0-9+\-_.]{1,24}')
TECH_TERM_RE = re.compile(r'(?<![A-Za-z0-9])[A-Z]{2,}[A-Z0-9+\-_.]*(?![A-Za-z0-9])')
CJK_TOKEN_RE = re.compile(r'[\u4e00-\u9fff]{2,12}(?:20\d{2})?')
PERSON_CANDIDATE_RE = re.compile(r'(?:[\u4e00-\u9fff]{2,4}|[A-Za-z][A-Za-z0-9+\-_.]*[\u4e00-\u9fff]{1,4})')
RANKED_PERSON_RE = re.compile(r'第[一二三四五六七八九十\d]+位是\s*[—-]*\s*([^\n。；]{2,32})')
INTRO_ROSTER_RE = re.compile(
    r'(?:有请|欢迎)[^\n。；—-]{0,40}[—-]{2,}\s*([^\n。；]{2,96})'
)
LATIN_NAME_RE = re.compile(r'[A-Za-z][A-Za-z0-9+\-_.]*(?: [A-Za-z][A-Za-z0-9+\-_.]*)*')
MIXED_CANONICAL_NAME_RE = re.compile(
    r'(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9+\-_.]{1,24})([\u4e00-\u9fff]{2,16})'
)
GLOSSARY_TERM_RE = re.compile(
    r'(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9+\-_.]*(?:\s+[A-Za-z][A-Za-z0-9+\-_.]*){0,2})'
    r'\s*[（(]([\u4e00-\u9fff]{1,12})[）)]'
)
LOCATION_PHRASE_RE = re.compile(r'[\u4e00-\u9fff]{1,8}(?:古镇|湾|市|区|县|镇|村|山|湖|河|园|街|路)')
SOURCE_SUFFIXES = {'.txt', '.md', '.srt', '.docx', '.pdf'}
LOCATION_SUFFIXES = ('古镇', '湾', '市', '区', '县', '镇', '村', '山', '湖', '河', '园', '街', '路')
PROGRAM_HINTS = ('节目', '大会', '赛事', '对决', '峰会', '访谈', '发布会', '202')
ORG_SUFFIXES = ('公司', '集团', '电视台', '频道', '中心', '研究院', '学院', '大学', '委员会', '协会', '工作室')
COMMON_STOPWORDS = {
    '这个', '那个', '我们', '你们', '他们', '大家', '就是', '然后', '因为', '所以',
    '但是', '如果', '一个', '没有', '可以', '不是', '还是', '现在', '今天', '这里',
}
GLOSSARY_DOMAIN_HINTS = ('风格', '伴奏', '腔调', '术语', '技巧', '唱法', '流派', '节奏')


@dataclass(frozen=True)
class ScriptTextExtraction:
    source_kind: str
    text: str
    status: str
    warning: str | None = None


def extract_script_document(payload: bytes, file_name: str) -> ScriptTextExtraction:
    suffix = Path(file_name or '').suffix.lower()
    if suffix and suffix not in SOURCE_SUFFIXES:
        raise ValueError('台本仅支持 txt、md、srt、docx、pdf')
    source_kind = suffix.lstrip('.') or 'txt'
    if suffix == '.docx':
        text = _extract_docx_text(payload)
    elif suffix == '.pdf':
        text = _extract_pdf_text(payload)
        if not text:
            return ScriptTextExtraction(
                source_kind='pdf',
                text='',
                status='unextractable',
                warning='PDF 台本没有可提取文本，可能是扫描件图片 PDF；首版不会静默执行 OCR',
            )
    else:
        text = _decode_text(payload)
        if suffix == '.srt':
            text = _strip_srt_noise(text)
    return ScriptTextExtraction(
        source_kind=source_kind,
        text=_normalize_source_text(text),
        status='ready',
    )


def extract_text_from_script_bytes(payload: bytes, file_name: str) -> str:
    extracted = extract_script_document(payload, file_name)
    if extracted.status != 'ready':
        raise ValueError(extracted.warning or '台本没有可提取文本')
    return _normalize_text(extracted.text)


def extract_script_context(text: str, source_name: Optional[str] = None) -> dict[str, Any]:
    normalized_text = _normalize_text(text)[:MAX_TEXT_CHARS]
    is_gold_srt = Path(str(source_name or '')).suffix.lower() == '.srt'
    reference_entries = _extract_reference_line_entries(
        normalized_text,
        limit=MAX_GOLD_SRT_REFERENCE_LINES if is_gold_srt else MAX_REFERENCE_LINES,
    )
    reference_lines = [line for line, _ends_physical_line in reference_entries]
    physical_line_ends = [
        ends_physical_line
        for _line, ends_physical_line in reference_entries
    ]
    terms: list[str] = []
    programs: list[str] = []
    people: list[str] = []
    places: list[str] = []
    organizations: list[str] = []
    canonical_terms = _extract_gold_srt_canonical_terms(normalized_text) if is_gold_srt else []

    people.extend(
        item['display_text']
        for item in canonical_terms
        if item.get('term_type') == 'person'
    )
    terms.extend(
        item['display_text']
        for item in canonical_terms
        if item.get('term_type') != 'person'
    )

    people.extend(_extract_ranked_people(normalized_text))

    for item in TITLE_RE.findall(normalized_text):
        cleaned = _clean_token(item)
        if cleaned:
            programs.append(cleaned)

    for token in LATIN_MIXED_RE.findall(normalized_text):
        cleaned = _clean_token(token)
        if cleaned:
            if _looks_like_person(cleaned):
                people.append(cleaned)
            else:
                terms.append(cleaned)

    for token in TECH_TERM_RE.findall(normalized_text):
        cleaned = _clean_token(token)
        if cleaned:
            terms.append(cleaned)

    for token in LOCATION_PHRASE_RE.findall(normalized_text):
        cleaned = _clean_location_token(token)
        if cleaned:
            places.append(cleaned)

    cjk_tokens = [_clean_token(item) for item in CJK_TOKEN_RE.findall(normalized_text)]
    cjk_tokens = [item for item in cjk_tokens if _is_meaningful_token(item)]
    frequencies = Counter(cjk_tokens)
    for token, _count in frequencies.most_common(120):
        if any(hint in token for hint in PROGRAM_HINTS):
            programs.append(token)
        elif token.endswith(ORG_SUFFIXES):
            organizations.append(token)
        elif token.endswith(LOCATION_SUFFIXES):
            places.append(token)
        elif _looks_like_person(token):
            people.append(token)
        else:
            terms.append(token)

    for token in PERSON_CANDIDATE_RE.findall(normalized_text):
        cleaned = _clean_token(token)
        if _looks_like_person(cleaned):
            people.append(cleaned)

    context = {
        'source_name': source_name,
        'source_names': [source_name] if source_name else [],
        'people': _dedupe_limited(people),
        'places': _dedupe_limited(places),
        'programs': _dedupe_limited(programs),
        'organizations': _dedupe_limited(organizations),
        'terms': _dedupe_limited(terms),
        'reference_lines': reference_lines,
        'reference_sources': [{
            'source_name': source_name,
            'source_hash': _reference_source_hash(source_name, reference_lines),
            'reference_lines': reference_lines,
            'physical_line_ends': physical_line_ends,
        }] if reference_lines else [],
        'canonical_terms': canonical_terms,
        'gold_reference': is_gold_srt,
    }
    keyword_count = sum(len(context[key]) for key in ('people', 'places', 'programs', 'organizations', 'terms'))
    context['summary'] = {
        'source_count': len(context['source_names']),
        'keyword_count': keyword_count,
        'reference_line_count': len(reference_lines),
        'canonical_term_count': len(canonical_terms),
        'text_length': len(normalized_text),
    }
    return context


def merge_script_contexts(*contexts: dict[str, Any] | None) -> dict[str, Any] | None:
    available = [context for context in contexts if context]
    if not available:
        return None

    source_names: list[str] = []
    merged: dict[str, Any] = {}
    for context in available:
        names = context.get('source_names') or [context.get('source_name')]
        for name in names:
            value = str(name or '').strip()
            if value and value not in source_names:
                source_names.append(value)

    for key in ('people', 'places', 'programs', 'organizations', 'terms'):
        merged[key] = _dedupe_limited(
            str(item or '').strip()
            for context in available
            for item in (context.get(key) or [])
        )

    merged['canonical_terms'] = _merge_canonical_terms(
        item
        for context in available
        for item in (context.get('canonical_terms') or [])
    )
    merged['gold_reference'] = any(bool(context.get('gold_reference')) for context in available)
    lyrics_references: list[dict[str, Any]] = []
    seen_lyrics_hashes: set[str] = set()
    for context in available:
        for item in context.get('lyrics_references') or []:
            if not isinstance(item, dict):
                continue
            reference_hash = str(item.get('reference_hash') or '').strip()
            dedupe_key = reference_hash or repr(item)
            if dedupe_key in seen_lyrics_hashes:
                continue
            seen_lyrics_hashes.add(dedupe_key)
            lyrics_references.append(item)
    merged['lyrics_references'] = lyrics_references
    merged['lyrics_correction_mode'] = any(
        bool(context.get('lyrics_correction_mode')) for context in available
    )

    reference_lines: list[str] = []
    reference_sources: list[dict[str, Any]] = []
    seen_reference_lines: set[str] = set()
    for context in available:
        for source in _context_reference_sources(context):
            source_lines: list[str] = []
            source_physical_line_ends: list[bool] = []
            physical_line_ends = source.get('physical_line_ends') or []
            for index, item in enumerate(source.get('reference_lines') or []):
                line = _clean_reference_line(str(item or ''))
                identity = _script_identity(line)
                if not line or not identity or identity in seen_reference_lines:
                    continue
                seen_reference_lines.add(identity)
                reference_lines.append(line)
                source_lines.append(line)
                source_physical_line_ends.append(
                    bool(physical_line_ends[index])
                    if index < len(physical_line_ends)
                    else False
                )
                if len(reference_lines) >= MAX_MERGED_REFERENCE_LINES:
                    break
            if source_lines:
                reference_sources.append({
                    'source_name': source.get('source_name'),
                    'source_hash': _valid_or_derived_source_hash(
                        source.get('source_hash'),
                        source.get('source_name'),
                        source.get('reference_lines') or [],
                    ),
                    'reference_lines': source_lines,
                    'physical_line_ends': source_physical_line_ends,
                })
            if len(reference_lines) >= MAX_MERGED_REFERENCE_LINES:
                break
        if len(reference_lines) >= MAX_MERGED_REFERENCE_LINES:
            break
    merged.update({
        'source_name': ' + '.join(source_names) or '合并台本',
        'source_names': source_names,
        'reference_lines': reference_lines,
        'reference_sources': reference_sources,
    })
    merged['summary'] = {
        'source_count': len(source_names),
        'keyword_count': sum(len(merged[key]) for key in ('people', 'places', 'programs', 'organizations', 'terms')),
        'reference_line_count': len(reference_lines),
        'canonical_term_count': len(merged['canonical_terms']),
        'lyrics_reference_count': len(lyrics_references),
        'lyrics_line_count': sum(int(item.get('line_count') or 0) for item in lyrics_references),
        'text_length': sum(int((context.get('summary') or {}).get('text_length') or 0) for context in available),
    }
    return merged


def extract_high_confidence_script_terms(context: dict[str, Any] | None) -> list[str]:
    """Extract explicit roster names without relying on capped keyword categories."""
    if not context:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = _clean_token(value)
        if (
            not cleaned
            or cleaned in seen
            or not _is_meaningful_token(cleaned)
        ):
            return
        seen.add(cleaned)
        terms.append(cleaned)

    def add_roster_capture(value: str) -> None:
        for item in re.split(r'[\u3001，,/\uff0f]+', value):
            cleaned = _clean_token(item)
            if not cleaned:
                continue
            add(cleaned)
            for cjk_name in re.findall(r'[\u4e00-\u9fff]{2,12}', cleaned):
                add(cjk_name)
            for latin_name in LATIN_NAME_RE.findall(cleaned):
                add(latin_name)

    for raw_line in context.get('reference_lines') or []:
        line = _clean_reference_line(str(raw_line or ''))
        if not line:
            continue
        for capture in RANKED_PERSON_RE.findall(line):
            add_roster_capture(capture)
        for capture in INTRO_ROSTER_RE.findall(line):
            add_roster_capture(capture)
    for item in context.get('canonical_terms') or []:
        if isinstance(item, dict):
            add(str(item.get('display_text') or ''))
    return terms


def format_script_context_for_prompt(context: dict[str, Any] | None) -> str:
    if not context:
        return ''
    lines: list[str] = []
    category_labels = [
        ('programs', '节目名'),
        ('people', '人名'),
        ('places', '地名'),
        ('organizations', '机构名'),
        ('terms', '专业词/易错词'),
    ]
    for key, label in category_labels:
        values = [str(item).strip() for item in context.get(key) or [] if str(item).strip()]
        if values:
            lines.append(f'{label}：{"、".join(values[:MAX_ITEMS_PER_CATEGORY])}')
    reference_lines = _compact_reference_lines_for_prompt(context.get('reference_lines') or [])
    if reference_lines:
        lines.append(
            '台本/唱词原文参考（按原顺序；歌词、英文大小写和英文单词空格优先以这里为准）：\n'
            + '\n'.join(f'- {line}' for line in reference_lines)
        )
    if not lines:
        return ''
    return (
        '台本辅助信息如下，请优先用于专名纠错、唱词纠错、英文空格恢复、断句和统一写法。'
        '不要据此新增音频里没有出现的大段内容，但当 ASR 与台本/唱词明显同音近似时，应优先采用台本写法：\n'
        + '\n'.join(lines)
    )


def _decode_text(payload: bytes) -> str:
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode('utf-8', errors='ignore')


def _extract_docx_text(payload: bytes) -> str:
    try:
        with zipfile.ZipFile(_bytes_to_seekable(payload)) as archive:
            document = archive.read('word/document.xml').decode('utf-8', errors='ignore')
    except Exception as exc:
        raise ValueError('无法读取 docx 台本正文') from exc
    document = re.sub(r'</w:p>', '\n', document)
    document = re.sub(r'</w:tab>', ' ', document)
    return _normalize_source_text(unescape(TAG_RE.sub('', document)))


def _extract_pdf_text(payload: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise ValueError('后端缺少 pypdf 依赖，无法读取 PDF 台本') from exc
    try:
        reader = PdfReader(_bytes_to_seekable(payload))
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    except Exception as exc:
        raise ValueError('无法读取 PDF 台本正文') from exc
    return _normalize_source_text(text)


def _bytes_to_seekable(payload: bytes):
    from io import BytesIO

    return BytesIO(payload)


def _strip_srt_noise(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if TIME_CODE_RE.match(line) or SRT_INDEX_RE.match(line):
            continue
        lines.append(line)
    return '\n'.join(lines)


def _extract_gold_srt_canonical_terms(text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    grouped: dict[str, list[tuple[str, str]]] = {}
    for latin, cjk_tail in MIXED_CANONICAL_NAME_RE.findall(text):
        grouped.setdefault(latin.casefold(), []).append((latin, cjk_tail))
    for variants in grouped.values():
        if len(variants) < 2:
            continue
        latin = Counter(item[0] for item in variants).most_common(1)[0][0]
        cjk = _common_prefix([item[1] for item in variants])[:8]
        if len(cjk) < 2:
            continue
        display_text = f'{latin}{cjk}'
        occurrence_count = len(variants)
        aliases = [item for item in (latin, cjk) if len(item) >= 2]
        result.append({
            'display_text': display_text,
            'term_type': 'person',
            'protection_level': 'hard',
            'aliases': list(dict.fromkeys(aliases)),
            'source': 'gold_srt_repetition',
            'occurrence_count': occurrence_count,
        })

    for match in GLOSSARY_TERM_RE.finditer(text):
        display_text = re.sub(r'\s+', ' ', match.group(1)).strip()
        gloss = match.group(2)
        if (
            not display_text[:1].isupper()
            or not any(hint in gloss for hint in GLOSSARY_DOMAIN_HINTS)
        ):
            continue
        result.append({
            'display_text': display_text,
            'term_type': 'term',
            'protection_level': 'hard',
            'aliases': [],
            'source': 'gold_srt_glossary',
            'occurrence_count': max(1, text.casefold().count(display_text.casefold())),
        })
    return _merge_canonical_terms(result)


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ''
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    return prefix


def _merge_canonical_terms(values) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        display_text = _clean_token(str(item.get('display_text') or ''))
        if len(display_text) < 2:
            continue
        key = display_text.casefold()
        aliases = [
            _clean_token(str(alias or ''))
            for alias in (item.get('aliases') or [])
            if len(_clean_token(str(alias or ''))) >= 2
        ]
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                'display_text': display_text,
                'term_type': str(item.get('term_type') or 'term'),
                'protection_level': str(item.get('protection_level') or 'hard'),
                'aliases': list(dict.fromkeys(aliases)),
                'source': str(item.get('source') or 'gold_srt'),
                'occurrence_count': int(item.get('occurrence_count') or 1),
            }
            continue
        existing['aliases'] = list(dict.fromkeys([*existing['aliases'], *aliases]))
        existing['occurrence_count'] = max(
            int(existing.get('occurrence_count') or 0),
            int(item.get('occurrence_count') or 0),
        )
    return sorted(
        merged.values(),
        key=lambda item: (item['term_type'] != 'person', -item['occurrence_count'], item['display_text'].casefold()),
    )


def _normalize_text(text: str) -> str:
    return normalize_to_simplified_chinese(_normalize_source_text(text))


def _normalize_source_text(text: str) -> str:
    text = str(text or '').replace('\ufeff', '')
    text = TAG_RE.sub('', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_reference_lines(text: str, *, limit: int = MAX_REFERENCE_LINES) -> list[str]:
    return [
        line
        for line, _ends_physical_line in _extract_reference_line_entries(
            text,
            limit=limit,
        )
    ]


def _extract_reference_line_entries(
    text: str,
    *,
    limit: int = MAX_REFERENCE_LINES,
) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for raw_line in str(text or '').splitlines():
        pieces = _split_reference_line(raw_line)
        for piece_index, piece in enumerate(pieces):
            cleaned = _clean_reference_line(piece)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            entries.append((cleaned, piece_index == len(pieces) - 1))
            if len(entries) >= limit:
                return entries
    return entries


def _split_reference_line(raw_line: str) -> list[str]:
    line = str(raw_line or '').strip()
    if not line:
        return []
    if len(line) <= REFERENCE_LINE_MAX_CHARS:
        return [line]
    pieces = [item.strip() for item in re.split(r'(?<=[。！？!?；;])', line) if item.strip()]
    if not pieces:
        pieces = [line[index:index + REFERENCE_LINE_MAX_CHARS] for index in range(0, len(line), REFERENCE_LINE_MAX_CHARS)]
    result: list[str] = []
    for piece in pieces:
        if len(piece) <= REFERENCE_LINE_MAX_CHARS:
            result.append(piece)
            continue
        result.extend(piece[index:index + REFERENCE_LINE_MAX_CHARS] for index in range(0, len(piece), REFERENCE_LINE_MAX_CHARS))
    return result


def _clean_reference_line(line: str) -> str:
    cleaned = TAG_RE.sub('', str(line or ''))
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' \t\r\n')
    if not cleaned or SRT_INDEX_RE.match(cleaned) or TIME_CODE_RE.match(cleaned):
        return ''
    return cleaned[:REFERENCE_LINE_MAX_CHARS]


def _compact_reference_lines_for_prompt(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    total_chars = 0
    for item in value:
        line = _clean_reference_line(str(item or ''))
        if not line:
            continue
        next_total = total_chars + len(line)
        if next_total > MAX_REFERENCE_TEXT_CHARS:
            break
        result.append(line)
        total_chars = next_total
    return result


def _clean_token(token: str) -> str:
    return str(token or '').strip(' \t\r\n，。！？；：、,.!?;:()（）[]【】<>《》"“”')


def _clean_location_token(token: str) -> str:
    cleaned = _clean_token(token)
    cleaned = re.sub(r'^(?:在|到|于|和|与|跟|去|来|从)', '', cleaned)
    for suffix in LOCATION_SUFFIXES:
        index = cleaned.find(suffix)
        if index >= 1:
            return cleaned[:index + len(suffix)]
    return cleaned


def _is_meaningful_token(token: str) -> bool:
    if len(token) < 2 or token in COMMON_STOPWORDS:
        return False
    if all(char in '一二三四五六七八九十百千万' for char in token):
        return False
    return True


def _looks_like_person(token: str) -> bool:
    if not token or len(token) > 12:
        return False
    if any(token.endswith(suffix) for suffix in LOCATION_SUFFIXES + ORG_SUFFIXES):
        return False
    if any(hint in token for hint in PROGRAM_HINTS):
        return False
    if re.search(r'[A-Za-z]', token) and re.search(r'[\u4e00-\u9fff]', token):
        return True
    return 2 <= len(token) <= 4 and token not in COMMON_STOPWORDS


def _extract_ranked_people(text: str) -> list[str]:
    people: list[str] = []
    for match in RANKED_PERSON_RE.findall(text):
        name = _clean_token(match)
        if not name or '_' in name:
            continue
        people.append(name)
        people.extend(re.findall(r'[\u4e00-\u9fff]{2,12}', name))
        people.extend(LATIN_NAME_RE.findall(name))
    return people


def _script_identity(value: str) -> str:
    simplified = normalize_to_simplified_chinese(str(value or ''))
    return re.sub(r'\s+', '', simplified).casefold()


def _reference_source_hash(source_name: object, lines: Iterable[object]) -> str:
    source_content = '\0'.join([
        str(source_name or '').strip(),
        *(str(line or '') for line in lines),
    ])
    return hashlib.sha256(source_content.encode('utf-8')).hexdigest()


def _valid_or_derived_source_hash(
    value: object,
    source_name: object,
    lines: Iterable[object],
) -> str:
    candidate = str(value or '').strip()
    if re.fullmatch(r'[0-9a-f]{64}', candidate):
        return candidate
    return _reference_source_hash(source_name, lines)


def _context_reference_sources(context: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [
        item
        for item in (context.get('reference_sources') or [])
        if isinstance(item, dict)
    ]
    if sources:
        return sources
    reference_lines = list(context.get('reference_lines') or [])
    if not reference_lines:
        return []
    source_name = context.get('source_name')
    return [{
        'source_name': source_name,
        'source_hash': _reference_source_hash(source_name, reference_lines),
        'reference_lines': reference_lines,
        'physical_line_ends': [False] * len(reference_lines),
    }]


def _dedupe_limited(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_token(value)
        identity = _script_identity(cleaned)
        if not cleaned or not identity or identity in seen or not _is_meaningful_token(cleaned):
            continue
        seen.add(identity)
        result.append(cleaned)
        if len(result) >= MAX_ITEMS_PER_CATEGORY:
            break
    return result
