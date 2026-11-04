from app.services.lexicon_tokenizer import (
    _TOKENIZER_CACHE,
    build_jieba_tokenizer,
    clear_tokenizer_cache,
)
from app.services.runtime_lexicon import BUNDLE_VERSION


def _bundle(term_set_hash: str, terms: list[dict]) -> dict:
    return {
        "bundle_version": BUNDLE_VERSION,
        "tenant_id": "default",
        "project_id": "project-a",
        "job_id": None,
        "term_set_hash": term_set_hash,
        "terms": terms,
        "disabled_terms": [],
        "source_counts": {},
        "protection_counts": {},
        "conflict_report": [],
        "generated_at": "2026-06-21T00:00:00Z",
    }


def test_jieba_tokenizer_keeps_bundle_terms_without_global_pollution() -> None:
    clear_tokenizer_cache()
    protected_bundle = _bundle(
        "hash-protected",
        [
            {"normalized_term": "北京元空智能科技有限公司", "display_text": "北京元空智能科技有限公司", "protection_level": "hard"},
            {"normalized_term": "原力无限", "display_text": "原力无限", "protection_level": "hard"},
        ],
    )
    protected_tokenizer = build_jieba_tokenizer(protected_bundle)

    protected_tokens = list(protected_tokenizer.cut("北京元空智能科技有限公司和原力无限来到现场", HMM=False))
    assert "北京元空智能科技有限公司" in protected_tokens
    assert "原力无限" in protected_tokens

    empty_tokenizer = build_jieba_tokenizer(_bundle("hash-empty", []))
    empty_tokens = list(empty_tokenizer.cut("北京元空智能科技有限公司和原力无限来到现场", HMM=False))

    assert empty_tokenizer is not protected_tokenizer
    assert not {"北京元空智能科技有限公司", "原力无限"}.issubset(empty_tokens)


def test_jieba_tokenizer_reuses_cache_and_evicts_oldest() -> None:
    clear_tokenizer_cache()
    first = build_jieba_tokenizer(
        _bundle(
            "hash-0",
            [{"normalized_term": "原力无限", "display_text": "原力无限", "protection_level": "hard"}],
        )
    )
    assert build_jieba_tokenizer(_bundle("hash-0", [])) is first

    for index in range(1, 66):
        build_jieba_tokenizer(_bundle(f"hash-{index}", []))

    assert len(_TOKENIZER_CACHE) == 64
    assert "hash-0" not in _TOKENIZER_CACHE
