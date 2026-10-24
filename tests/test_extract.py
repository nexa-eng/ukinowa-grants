from datetime import date

import pytest

from grants.extract import GRANT_SCHEMA, MAX_PROGRAMS, PROGRAM_SCHEMA, ModelStopped, parse_result, prefilter, rule_based
from grants.fetch import RawItem

PROFILE = {
    "prefilter_keywords": ["助成", "補助"],
    "exclude_keywords": ["入札", "求人"],
    "exclude_url_patterns": ["/sitemap", "/tag/"],
}


def item(title, url="https://example.jp/p/1", summary=""):
    return RawItem(source_id="s", source_name="S", title=title, url=url, summary=summary)


def test_prefilter_keyword_source_requires_keyword():
    src = {"id": "s", "dedicated": False}
    assert prefilter(item("○○助成金の募集"), src, PROFILE)
    assert not prefilter(item("ごみ収集カレンダー"), src, PROFILE)


def test_prefilter_dedicated_html_source_limits_to_base_path_or_keyword():
    src = {"id": "s", "dedicated": True, "kind": "html", "url": "https://example.jp/subsidy/"}
    assert prefilter(item("第3回 のお知らせ", url="https://example.jp/subsidy/2026/"), src, PROFILE)
    assert prefilter(item("○○助成のお知らせ", url="https://example.jp/news/1"), src, PROFILE)
    assert not prefilter(item("個人情報の保護について", url="https://example.jp/privacy-policy/"), src, PROFILE)


def test_prefilter_dedicated_rss_source_passes_without_keyword_but_respects_exclusions():
    src = {"id": "s", "dedicated": True, "kind": "rss", "url": "https://example.jp/feed/"}
    assert prefilter(item("第3回 公募のお知らせ"), src, PROFILE)
    assert not prefilter(item("職員の求人"), src, PROFILE)
    assert not prefilter(item("公募のお知らせ", url="https://example.jp/tag/news"), src, PROFILE)


def test_prefilter_region_filter():
    src = {"id": "s", "dedicated": True, "region_filter": ["熊本", "全国"]}
    assert prefilter(item("【熊本県】補助金"), src, PROFILE)
    assert not prefilter(item("【北海道】補助金"), src, PROFILE)


def test_rule_based_extracts_deadline_from_text():
    [r] = rule_based(item("助成"), "申請締切は 2026年11月20日 です", date(2026, 10, 1))
    assert r["deadline"] == "2026-11-20"
    [r2] = rule_based(item("助成"), "2026年12月1日 必着", date(2026, 10, 1))
    assert r2["deadline"] == "2026-12-01"
    [r3] = rule_based(item("助成"), "締切は未定です", date(2026, 10, 1))
    assert r3["deadline"] is None


def test_prefilter_keyword_source_passes_nippon_foundation_call_and_skips_other_news():
    src = {"id": "nippon-foundation-news", "dedicated": False, "kind": "html",
           "url": "https://www.nippon-foundation.or.jp/who/news/information"}
    call = item("<助成事業募集>2027年度助成金交付申請に関する募集",
                url="https://www.nippon-foundation.or.jp/who/news/information/2026/20260915-125201.html")
    assert prefilter(call, src, PROFILE)
    assert not prefilter(item("会長の年頭所感"), src, PROFILE)


NF_URL = "https://www.nippon-foundation.or.jp/who/news/information/2026/20260915-125201.html"


def program(title, apply_url="https://nippon-foundation.my.site.com/GrantPrograms/s/assistanceprogram/x/", **kw):
    p = {"title": title, "provider": "日本財団", "summary": "s", "deadline": "2026-10-30", "deadline_note": "",
         "amount_max_yen": None, "amount_note": "", "eligible_types": ["一般社団法人"], "region_scope": "全国",
         "themes": [], "fit_programs": ["03"], "fit_score": 40, "fit_reason": "r", "apply_url": apply_url,
         "program_key": f"日本財団|{title}"}
    p.update(kw)
    return p


def test_parse_result_returns_one_record_per_program():
    raw = {"is_grant_program": True, "programs": [program("2027年度 子ども第三の居場所"), program("2027年度 公益・福祉募集")]}
    out = parse_result(raw, NF_URL, date(2026, 10, 24))
    assert [p["title"] for p in out] == ["2027年度 子ども第三の居場所", "2027年度 公益・福祉募集"]
    assert all(p["is_grant_program"] and p["deadline"] == "2026-10-30" for p in out)
    # 別サイトの申請リンクは採用せず、元ページに戻す
    assert all(p["apply_url"] == NF_URL for p in out)


def test_parse_result_not_grant_is_empty_and_inconsistent_output_is_retried():
    assert parse_result({"is_grant_program": False, "programs": []}, NF_URL, date(2026, 10, 24)) == []
    with pytest.raises(ModelStopped):
        parse_result({"is_grant_program": True, "programs": []}, NF_URL, date(2026, 10, 24))


def test_parse_result_caps_program_count():
    raw = {"is_grant_program": True, "programs": [program(f"制度{i}") for i in range(MAX_PROGRAMS + 5)]}
    assert len(parse_result(raw, NF_URL, date(2026, 10, 24))) == MAX_PROGRAMS


def _union_params(schema) -> int:
    """構造化出力の上限（null 可などの union を持つ項目は16個まで）を数える。"""
    n = 0
    if isinstance(schema, dict):
        if isinstance(schema.get("type"), list) or "anyOf" in schema:
            n += 1
        for v in schema.values():
            n += _union_params(v)
    elif isinstance(schema, list):
        n += sum(_union_params(v) for v in schema)
    return n


def test_grant_schema_stays_within_union_limit():
    assert _union_params(GRANT_SCHEMA) <= 16
    assert GRANT_SCHEMA["properties"]["programs"]["items"] is PROGRAM_SCHEMA
