from datetime import date

from grants.build import group_grants
from grants.run import store_programs

PAGE = "https://www.nippon-foundation.or.jp/who/news/information/2026/20260915-125201.html"
SRC = {"id": "nippon-foundation-news", "name": "日本財団 お知らせ", "dedicated": False, "public_ok": True}
NF_PROGRAMS = ["2027年度 海洋・船舶募集", "2027年度 公益・福祉募集", "2027年度 子ども第三の居場所",
               "2027年度 多機能型児童家庭支援センターの新築・改修", "2027年度 犯罪被害者等支援事業（預保納付金支援事業）"]


def program(title, fit=40):
    return {"title": title, "provider": "日本財団", "summary": "s", "deadline": "2026-10-30", "fit_score": fit,
            "program_key": f"日本財団|{title}", "is_grant_program": True}


def test_store_programs_keeps_first_at_page_url_and_numbers_the_rest():
    grants: dict = {}
    records = store_programs(grants, PAGE, [program(t) for t in NF_PROGRAMS], SRC, date(2026, 10, 26))
    assert [r["url"] for r in records] == [PAGE] + [f"{PAGE}#program-{i}" for i in range(2, 6)]
    assert set(grants) == {r["url"] for r in records}
    assert all(r["first_seen"] == "2026-10-26" and r["source_id"] == "nippon-foundation-news" for r in records)


def test_store_programs_reevaluation_drops_stale_records_and_keeps_first_seen():
    grants: dict = {"https://other.jp/1": {"url": "https://other.jp/1"}}
    store_programs(grants, PAGE, [program(t) for t in NF_PROGRAMS], SRC, date(2026, 10, 26))
    store_programs(grants, PAGE, [program(NF_PROGRAMS[0]), program(NF_PROGRAMS[1])], SRC, date(2026, 11, 2))
    assert set(grants) == {"https://other.jp/1", PAGE, f"{PAGE}#program-2"}
    assert grants[PAGE]["first_seen"] == "2026-10-26" and grants[PAGE]["last_checked"] == "2026-11-02"


def test_group_grants_keeps_nippon_foundation_programs_separate():
    grants: dict = {}
    store_programs(grants, PAGE, [program(t) for t in NF_PROGRAMS], SRC, date(2026, 10, 26))
    assert len(group_grants(list(grants.values()))) == len(NF_PROGRAMS)
