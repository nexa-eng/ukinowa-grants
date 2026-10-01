from datetime import date

from grants.extract import prefilter, rule_based
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


def test_prefilter_dedicated_source_passes_without_keyword_but_respects_exclusions():
    src = {"id": "s", "dedicated": True}
    assert prefilter(item("第3回 公募のお知らせ"), src, PROFILE)
    assert not prefilter(item("職員の求人"), src, PROFILE)
    assert not prefilter(item("公募のお知らせ", url="https://example.jp/tag/news"), src, PROFILE)


def test_prefilter_region_filter():
    src = {"id": "s", "dedicated": True, "region_filter": ["熊本", "全国"]}
    assert prefilter(item("【熊本県】補助金"), src, PROFILE)
    assert not prefilter(item("【北海道】補助金"), src, PROFILE)


def test_rule_based_extracts_deadline_from_text():
    r = rule_based(item("助成"), "申請締切は 2026年11月20日 です", date(2026, 10, 1))
    assert r["deadline"] == "2026-11-20"
    r2 = rule_based(item("助成"), "2026年12月1日 必着", date(2026, 10, 1))
    assert r2["deadline"] == "2026-12-01"
    r3 = rule_based(item("助成"), "締切は未定です", date(2026, 10, 1))
    assert r3["deadline"] is None
