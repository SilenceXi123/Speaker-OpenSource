from app.services.chinese_text import normalize_to_simplified_chinese


def test_normalize_to_simplified_chinese_converts_common_subtitle_traditional_text() -> None:
    assert normalize_to_simplified_chinese('這裡就是瀋陽的國際賽艇中心哦') == '这里就是沈阳的国际赛艇中心哦'
    assert normalize_to_simplified_chinese('這個行業的觀察呀') == '这个行业的观察呀'
    assert normalize_to_simplified_chinese('非正式的會議當中慢慢地產生的') == '非正式的会议当中慢慢地产生的'


def test_normalize_to_simplified_chinese_preserves_non_chinese_display_text() -> None:
    assert normalize_to_simplified_chinese('臺灣 AI Model 2026，OK!') == '台湾 AI Model 2026，OK!'
