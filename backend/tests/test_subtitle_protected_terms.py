from app.services.subtitle_protected_terms import resolve_subtitle_protected_terms


def test_resolve_subtitle_protected_terms_merges_builtin_script_and_extra_terms() -> None:
    script_context = {
        'terms': ['蓝海数智体验馆', '这个'],
        'reference_lines': [
            '这里就是沈阳的国际赛艇中心哦',
            '我们现在一起进入蓝海数智体验馆参观',
        ],
    }

    terms = resolve_subtitle_protected_terms(
        script_context,
        extra_terms=['工作人员'],
    )

    assert '工作人员' in terms
    assert '国际赛艇中心' in terms
    assert '蓝海数智体验馆' in terms
    assert '这个' not in terms
    assert terms.index('蓝海数智体验馆') < terms.index('工作人员')


def test_script_keyword_fragment_before_latin_code_is_not_protected() -> None:
    terms = resolve_subtitle_protected_terms({
        'people': ['传感器和E'],
        'terms': ['M系列'],
    })

    assert '传感器和E' not in terms
    assert 'M系列' in terms


def test_repeated_boundary_checks_do_not_reconvert_the_same_short_terms(monkeypatch) -> None:
    from app.services import subtitle_protected_terms as protection
    from app.services.subtitles import _boundary_inside_protected_term

    caches = [getattr(protection, name, None) for name in ('_normalize_short_protected_term', '_resolve_short_protected_terms')]
    for cached in caches:
        if cached:
            cached.cache_clear()
    conversions = []
    validations = []
    original = protection.normalize_to_simplified_chinese
    valid = protection._is_valid_protected_term

    def convert(text):
        conversions.append(text)
        return original(text)

    monkeypatch.setattr(protection, 'normalize_to_simplified_chinese', convert)

    def validate(term):
        validations.append(term)
        return valid(term)

    monkeypatch.setattr(protection, '_is_valid_protected_term', validate)
    try:
        for _ in range(25):
            assert _boundary_inside_protected_term('来自华为和同济大学', 3, protected_terms=('華為', '同濟大學'))
            assert not _boundary_inside_protected_term('来自华为和同济大学', 4, protected_terms=('華為', '同濟大學'))
        assert conversions.count('華為') == 1
        assert conversions.count('同濟大學') == 1
        assert validations.count('华为') == 1
        different = resolve_subtitle_protected_terms(extra_terms=('星河科技',))
        assert '星河科技' in different
        assert '同济大学' not in different
    finally:
        for cached in caches:
            if cached:
                cached.cache_clear()


def test_large_invalid_script_fragments_are_not_retained_by_term_cache(monkeypatch) -> None:
    from app.services import subtitle_protected_terms as protection

    conversions = []
    original = protection.normalize_to_simplified_chinese

    def convert(text):
        conversions.append(text)
        return original(text)

    monkeypatch.setattr(protection, 'normalize_to_simplified_chinese', convert)
    fragment = '臺灣' * 100
    assert resolve_subtitle_protected_terms(extra_terms=(fragment,), builtin_terms=()) == ()
    assert resolve_subtitle_protected_terms(extra_terms=(fragment,), builtin_terms=()) == ()
    assert conversions == [fragment, fragment]
