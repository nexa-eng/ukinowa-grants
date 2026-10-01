from datetime import date

from grants.validate import safe_url, same_site, sanitize_grant, valid_deadline, clean_text

TODAY = date(2026, 10, 1)


def test_safe_url_rejects_dangerous_schemes_and_control_chars():
    assert safe_url("https://example.jp/a?b=1") == "https://example.jp/a?b=1"
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("data:text/html,hi") is None
    assert safe_url("https://example.jp/a\r\nX-Injected: 1") is None
    assert safe_url("httpx://example.jp") is None
    assert safe_url(None) is None


def test_same_site_handles_japanese_second_level_domains():
    assert same_site("https://www.pref.kumamoto.jp/a", "https://www.pref.kumamoto.jp/b")
    assert same_site("https://a.akaihane.or.jp/x", "https://www.akaihane.or.jp/y")
    assert not same_site("https://evil.example/x", "https://www.akaihane.or.jp/y")


def test_valid_deadline_range_and_format():
    assert valid_deadline("2026-11-20", TODAY) == "2026-11-20"
    assert valid_deadline("9999-12-31", TODAY) is None
    assert valid_deadline("2026/11/20", TODAY) is None
    assert valid_deadline("2026-02-30", TODAY) is None


def test_sanitize_grant_clamps_and_falls_back_apply_url():
    src = "https://www.akaihane.or.jp/subsidies/x/"
    out = sanitize_grant({
        "is_grant_program": True, "title": "A\r\nB" + "x" * 500, "provider": "P", "summary": "S",
        "deadline": "9999-12-31", "deadline_note": "", "amount_max_yen": 10**15, "amount_note": "",
        "eligible_types": ["任意団体"] * 20, "region_scope": "全国", "themes": [], "fit_programs": ["01", "99"],
        "fit_score": 999, "fit_reason": "r", "apply_url": "https://phish.example/apply",
    }, src, TODAY)
    assert out["fit_score"] == 100
    assert out["deadline"] is None
    assert out["amount_max_yen"] is None
    assert out["apply_url"] == src
    assert out["fit_programs"] == ["01"]
    assert len(out["eligible_types"]) == 10
    assert "\r" not in out["title"] and len(out["title"]) <= 200


def test_clean_text_strips_control_characters():
    assert clean_text("a\x00b\x1fc  d\n e") == "abc d e"


def test_program_key_ignores_year_round_and_noise():
    from grants.validate import program_key
    a = program_key("令和9年度 キリン・地域のちから応援事業 公募助成", "公益財団法人キリン福祉財団")
    b = program_key("キリン福祉財団 公募（キリン・地域のちから応援事業）", "キリン福祉財団")
    assert a.split("|")[1] == b.split("|")[1]
    assert program_key("第32次災害救援活動助成金", "全日本仏教会") == program_key("第３１次 災害救援活動助成金", "全日本仏教会")
