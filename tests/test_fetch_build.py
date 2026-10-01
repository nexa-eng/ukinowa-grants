import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from grants.build import _ics_escape, _ics_fold, build_all, build_collected
from grants.fetch import decode


def test_decode_prefers_declared_then_meta_then_cp932():
    sjis = "熊本県".encode("cp932")
    assert decode(sjis, "cp932") == "熊本県"
    assert decode(b'<meta charset="Shift_JIS">' + sjis, "ISO-8859-1").endswith("熊本県")
    assert decode("熊本県".encode("utf-8"), None) == "熊本県"


def test_ics_escape_and_fold():
    assert _ics_escape("a;b,c\\d\r\ne") == "a\;b\\,c\\\\d\\ne"
    folded = _ics_fold("SUMMARY:" + "あ" * 80)
    assert all(len(line.encode("utf-8")) <= 75 for line in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == "SUMMARY:" + "あ" * 80


def test_build_all_writes_outputs_and_filters(tmp_path: Path):
    now = datetime(2026, 10, 1, 5, 0, tzinfo=timezone(timedelta(hours=9)))
    today = now.date()
    grants = [
        {"url": "https://a.jp/1", "title": "良い助成", "provider": "P", "summary": "S", "deadline": "2026-11-20",
         "deadline_note": "", "amount_max_yen": 1000000, "amount_note": "", "eligible_types": ["任意団体"],
         "region_scope": "全国", "themes": [], "fit_programs": ["01"], "fit_score": 80, "fit_reason": "r",
         "apply_url": "javascript:alert(1)", "source_name": "S", "status": "open"},
        {"url": "https://a.jp/2", "title": "低い助成", "provider": "P", "summary": "S", "deadline": None,
         "deadline_note": "", "amount_max_yen": None, "amount_note": "", "eligible_types": [], "region_scope": "全国",
         "themes": [], "fit_programs": [], "fit_score": 10, "fit_reason": "r", "apply_url": "", "source_name": "S", "status": "open"},
    ]
    profile = {"closing_soon_days": 60, "alarm_days_before": [21, 7], "min_fit_public": 30}
    report = build_all(grants, {"https://a.jp/1"}, today, now, tmp_path, profile)
    index = (tmp_path / "index.html").read_text()
    assert "良い助成" in index and "低い助成" not in index
    assert "javascript:" not in index and 'href="https://a.jp/1"' in index
    assert "Content-Security-Policy" in index
    assert report.name == "2026-10-01.html"
    data = json.loads((tmp_path / "grants.json").read_text())
    assert [g["url"] for g in data] == ["https://a.jp/1"]
    ics = (tmp_path / "grants.ics").read_text()
    assert ics.count("BEGIN:VEVENT") == 3 and "DTSTART;VALUE=DATE:20261120" in ics
    build_collected([{"source_name": "S", "title": "t", "url": "https://a.jp/1", "decision": "助成", "fit_score": 80, "public_ok": True},
                     {"source_name": "S", "title": "hidden", "url": "https://a.jp/3", "decision": "判定へ", "fit_score": None, "public_ok": False}],
                    today, now, tmp_path)
    log = (tmp_path / "collected" / "2026-10-01.html").read_text()
    assert "hidden" not in log and 'class="dec"' in log
