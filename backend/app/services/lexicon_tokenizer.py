from __future__ import annotations

from collections import OrderedDict
import time
from typing import Any


_TOKENIZER_CACHE: OrderedDict[str, tuple[float, object]] = OrderedDict()
_TOKENIZER_CACHE_MAX_SIZE = 64
_TOKENIZER_CACHE_TTL_SECONDS = 6 * 60 * 60
_PROTECTION_FREQ = {
    "hard": 2_000_000,
    "strong": 1_000_000,
    "soft": 200_000,
}


def clear_tokenizer_cache() -> None:
    _TOKENIZER_CACHE.clear()


def build_jieba_tokenizer(bundle: dict[str, Any]) -> object:
    import jieba

    now = time.time()
    _expire_tokenizers(now)
    cache_key = str(bundle["term_set_hash"])
    cached = _TOKENIZER_CACHE.get(cache_key)
    if cached is not None:
        _TOKENIZER_CACHE.move_to_end(cache_key)
        return cached[1]

    tokenizer = jieba.Tokenizer()
    for term in bundle.get("terms", []):
        normalized_term = str(term.get("normalized_term") or "").strip()
        if not normalized_term:
            continue
        freq = _PROTECTION_FREQ.get(str(term.get("protection_level") or "soft"), _PROTECTION_FREQ["soft"])
        tokenizer.add_word(normalized_term, freq=freq)

    _TOKENIZER_CACHE[cache_key] = (now, tokenizer)
    _evict_oldest_tokenizers()
    return tokenizer


def _expire_tokenizers(now: float) -> None:
    expired_keys = [
        cache_key
        for cache_key, (created_at, _tokenizer) in _TOKENIZER_CACHE.items()
        if now - created_at > _TOKENIZER_CACHE_TTL_SECONDS
    ]
    for cache_key in expired_keys:
        _TOKENIZER_CACHE.pop(cache_key, None)


def _evict_oldest_tokenizers() -> None:
    while len(_TOKENIZER_CACHE) > _TOKENIZER_CACHE_MAX_SIZE:
        _TOKENIZER_CACHE.popitem(last=False)
