from __future__ import annotations


SPLIT_OBJECT_HEADS = (
    '一个',
    '一种',
    '一些',
    '一位',
    '一名',
    '养老',
    '方案',
    '瓶颈',
    '动作',
    '感触',
    '空间',
)
SPLIT_OBJECT_COMPLEMENT_TAILS = (
    '一直是',
    '总是',
    '还是',
    '也是',
)
SPLIT_PREDICATE_TAILS = (
    '解决',
    '处理',
    '提供',
    '满足',
    '实现',
    '完成',
    '学习',
    '投下',
    '打通',
    '维持',
    '形成',
    '产生',
)
BOUND_LEFT_TAILS = (
    '的',
    '地',
    '得',
    '把',
    '被',
    '在',
    '从',
    '向',
    '对',
    '为',
    '与',
    '和',
    '加',
    '及',
    '或',
    '通过',
    '采用',
    '使用',
    '由',
    '将',
    '给',
    '让',
)
COMPOUND_CONTINUATION_HEADS = (
    '加',
    '融合',
    '感知',
    '技术',
    '传感器',
    '信息',
    '系统',
    '产品',
    '数据',
    '方案',
    '能力',
    '模块',
    '路线',
)
PARALLEL_NOUN_HEADS = (
    '信息',
    '技术',
    '系统',
    '数据',
    '能力',
    '方案',
    '产品',
    '材料',
)
CLAUSE_PUNCTUATION = '，,。！？!?；;：:、'
BOUND_PUNCTUATION_HEADS = '，,。！？!?；;：:'
DEPENDENT_CONJUNCTION_HEADS = ('和', '与', '及', '或')
LEADING_PREDICATE_HEADS = ('采用', '使用', '通过', '依靠', '利用', '借助')
BOUND_LEFT_LEXICAL_EXCEPTIONS = (
    '成为',
    '认为',
    '存在',
    '现在',
    '所在',
    '面向',
    '走向',
    '方向',
    '饱和',
    '共和',
    '涉及',
)


def grammar_boundary_penalty(prefix: str, suffix: str) -> int:
    penalty = 0
    if _starts_with_bound_punctuation(suffix):
        penalty += 1000
    if _starts_with_dependent_conjunction(prefix, suffix):
        penalty += 220
    if _splits_leading_predicate(prefix, suffix):
        penalty += 400
    if _strands_object_complement(prefix, suffix):
        penalty += 72
    if _strands_predicate_object(prefix, suffix):
        penalty += 48
    if _strands_function_word(prefix):
        penalty += 220
    if _strands_compound_nominal(prefix, suffix):
        penalty += 180
    if _strands_parallel_noun(prefix, suffix):
        penalty += 220
    return penalty


def _starts_with_bound_punctuation(text: str) -> bool:
    value = str(text or '').lstrip()
    return bool(value and value[0] in BOUND_PUNCTUATION_HEADS)


def _starts_with_dependent_conjunction(prefix: str, suffix: str) -> bool:
    left = str(prefix or '').rstrip()
    right = str(suffix or '').lstrip()
    return bool(
        left
        and right.startswith(DEPENDENT_CONJUNCTION_HEADS)
        and left[-1] not in '。！？!?；;'
    )


def starts_with_object_head(text: str) -> bool:
    return any(str(text or '').startswith(head) for head in SPLIT_OBJECT_HEADS)


def _splits_leading_predicate(prefix: str, suffix: str) -> bool:
    left = str(prefix or '').rstrip()
    right = str(suffix or '').lstrip()
    return bool(
        left
        and left[-1] not in CLAUSE_PUNCTUATION
        and right.startswith(LEADING_PREDICATE_HEADS)
    )


def _strands_object_complement(prefix: str, suffix: str) -> bool:
    left = str(prefix or '')
    right = str(suffix or '')
    if not left or not right:
        return False
    return left.endswith(SPLIT_OBJECT_COMPLEMENT_TAILS) and starts_with_object_head(right)


def _strands_predicate_object(prefix: str, suffix: str) -> bool:
    left = str(prefix or '')
    right = str(suffix or '')
    if not left or not right:
        return False
    if right[0] in '但并而却也又再先还':
        return False
    return left.endswith(SPLIT_PREDICATE_TAILS) and starts_with_object_head(right)


def _strands_function_word(prefix: str) -> bool:
    left = str(prefix or '').rstrip()
    return bool(
        left
        and left[-1] not in CLAUSE_PUNCTUATION
        and not left.endswith(BOUND_LEFT_LEXICAL_EXCEPTIONS)
        and left.endswith(BOUND_LEFT_TAILS)
    )


def _strands_compound_nominal(prefix: str, suffix: str) -> bool:
    left = str(prefix or '').rstrip()
    right = str(suffix or '').lstrip()
    if not left or not right or left[-1] in CLAUSE_PUNCTUATION:
        return False
    if right.startswith(('但', '并', '而', '却', '也', '又', '再', '还')):
        return False
    return right.startswith(COMPOUND_CONTINUATION_HEADS)


def _strands_parallel_noun(prefix: str, suffix: str) -> bool:
    left = str(prefix or '').rstrip()
    right = str(suffix or '').lstrip()
    if not left or not right or left[-1] in CLAUSE_PUNCTUATION:
        return False
    conjunction = next(
        (item for item in ('和', '与', '及', '或') if right.startswith(item)),
        None,
    )
    if conjunction is None:
        return False
    right_tail = right[len(conjunction):]
    return any(left.endswith(head) and head in right_tail[:10] for head in PARALLEL_NOUN_HEADS)
