"""応募可否の判定日（decide_by）。締切の 21 日前が基本で、見つけた日より前にはしない。"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from grants.build import apply_decide, build_all, decide_by, group_grants

PROFILE = {"decide_days_before": 21, "decide_grace_days": 3, "decide_margin_days": 3}


def grant(first_seen, deadline, **extra):
    return {"url": "https://a.jp/1", "title": "助成", "first_seen": first_seen, "deadline": deadline, **extra}


def test_uses_21_days_before_the_deadline_when_there_is_time():
    assert decide_by(grant("2026-10-01", "2026-12-10"), PROFILE) == ("2026-11-19", None)


def test_keeps_21_days_before_when_it_is_exactly_the_grace_after_first_seen():
    assert decide_by(grant("2026-11-16", "2026-12-10"), PROFILE) == ("2026-11-19", None)


def test_moves_a_past_decision_day_to_three_days_after_first_seen():
    # ボラサポ第2回中長期: 11-16 に見つけ、締切 11-27。21 日前の 11-06 は見つけた日より前
    assert decide_by(grant("2026-11-16", "2026-11-27"), PROFILE) == ("2026-11-19", 11)


def test_never_later_than_three_days_before_the_deadline():
    assert decide_by(grant("2026-11-16", "2026-11-20"), PROFILE) == ("2026-11-17", 4)


def test_uses_first_seen_when_the_deadline_is_too_close():
    assert decide_by(grant("2026-11-16", "2026-11-18"), PROFILE) == ("2026-11-16", 2)


def test_without_first_seen_falls_back_to_21_days_before():
    assert decide_by(grant(None, "2026-11-27"), PROFILE) == ("2026-11-06", None)


def test_without_deadline_there_is_no_decision_day():
    assert decide_by(grant("2026-11-16", None), PROFILE) == (None, None)
    assert decide_by(grant("2026-11-16", "未定"), PROFILE) == (None, None)


def test_defaults_match_the_profile():
    assert decide_by(grant("2026-11-16", "2026-11-27"), {}) == ("2026-11-19", 11)


def test_apply_decide_sets_both_fields():
    g = apply_decide(grant("2026-11-16", "2026-11-27"), PROFILE)
    assert (g["decide_by"], g["decide_rush_days"]) == ("2026-11-19", 11)


def test_group_uses_the_earliest_first_seen():
    a = {"url": "https://a.jp/1", "title": "ボラサポ 第2回中長期助成", "provider": "中央共同募金会", "fit_score": 90,
         "deadline": "2026-11-27", "first_seen": "2026-11-16", "summary": "x", "source_name": "A"}
    b = {**a, "url": "https://b.jp/2", "fit_score": 70, "first_seen": "2026-11-09", "source_name": "B"}
    (rec,) = group_grants([a, b])
    assert rec["url"] == "https://a.jp/1" and rec["first_seen"] == "2026-11-09"


def test_card_marks_a_rushed_decision(tmp_path: Path):
    now = datetime(2026, 11, 16, 5, 0, tzinfo=timezone(timedelta(hours=9)))
    g = {"url": "https://a.jp/1", "title": "急ぎの助成", "provider": "P", "summary": "S", "deadline": "2026-11-27",
         "deadline_note": "", "amount_max_yen": 3000000, "amount_note": "", "eligible_types": ["任意団体"],
         "region_scope": "全国", "themes": [], "fit_programs": ["01"], "fit_score": 90, "fit_reason": "r",
         "apply_url": "", "source_name": "S", "status": "open", "first_seen": "2026-11-16"}
    profile = {"closing_soon_days": 60, "alarm_days_before": [21, 7], "min_fit_public": 30, **PROFILE}
    report = build_all([g], {"https://a.jp/1"}, now.date(), now, tmp_path, profile)
    page = report.read_text()
    assert "応募可否の判定日 2026-11-19" in page
    assert "・急ぎ: 見つけた時に締切まで 11 日" in page
