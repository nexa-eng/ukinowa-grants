"""週次の実行入口。

python -m grants.run --llm                AI 判定あり（本番）
python -m grants.run --collect-only       収集と規則の絞り込みだけ（判定・状態更新・通知はしない）
python -m grants.run --no-llm --no-mail   規則のみ（ローカル確認用）
python -m grants.run --llm --max-llm 20   AI 判定の件数を抑える

終了コード: 0 = 正常。1 = 情報源が全滅、判定対象があるのに1件も判定できない、メール送信失敗 のいずれか。
状態は10件ごとに保存するので、途中で止まっても進捗は残る（ワークフローは常にコミットする）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import build, extract, fetch, notify

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
# 生成物（state, docs）は data ブランチに置く。GitHub Actions では DATA_ROOT にそのチェックアウト先を渡す
DATA_ROOT = Path(os.environ.get("DATA_ROOT", ROOT))
STATE = DATA_ROOT / "state"
DOCS = DATA_ROOT / "docs"
JST = timezone(timedelta(hours=9))

MAX_LLM_HARD_CAP = 300
FAILURES_BEFORE_GIVE_UP = 3      # 取得・判定に3回失敗した URL は seen に入れて諦める
SEEN_RECHECK_DAYS = 365          # 「助成ではない」と判定した URL は1年後にもう一度見る（年次更新の取りこぼし対策）
CLOSED_RECHECK_DAYS = 300        # 締切済みの助成の URL は約10か月後にもう一度見る
CHECKPOINT_EVERY = 10


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def pages_base_url() -> str:
    explicit = os.environ.get("PAGES_URL")
    if explicit:
        return explicit.rstrip("/")
    owner, name = os.environ.get("GITHUB_REPOSITORY", "nexa-eng/ukinowa-grants").split("/", 1)
    return f"https://{owner}.github.io/{name}"


def _days_left(deadline, today):
    try:
        return (date.fromisoformat(deadline) - today).days if deadline else None
    except ValueError:
        return None


def _age_days(iso: str | None, today: date) -> int:
    try:
        return (today - date.fromisoformat(iso)).days if iso else 10**6
    except ValueError:
        return 10**6


def _parse_max(value: str | None, default: int) -> int:
    try:
        n = int(float(value)) if value not in (None, "") else default
    except ValueError:
        n = default
    return max(0, min(n, MAX_LLM_HARD_CAP))


def _interleave(candidates: list[tuple[fetch.RawItem, dict]]) -> list[tuple[fetch.RawItem, dict]]:
    """情報源ごとに順番に取り出す。上限で切れても特定の情報源だけが飢えないようにする。"""
    by_src: dict[str, list] = {}
    for c in candidates:
        by_src.setdefault(c[1]["id"], []).append(c)
    out: list = []
    while any(by_src.values()):
        for q in by_src.values():
            if q:
                out.append(q.pop(0))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--llm", action="store_true", help="Claude で判定する（本番）")
    g.add_argument("--no-llm", action="store_true", help="規則だけで判定する（確認用）")
    ap.add_argument("--max-llm", type=int, default=_parse_max(os.environ.get("MAX_LLM"), 120), help="1回の実行で判定する上限件数（0〜300）")
    ap.add_argument("--no-mail", action="store_true")
    ap.add_argument("--collect-only", action="store_true", help="収集と規則の絞り込みだけ行い、判定・状態更新・通知はしない")
    args = ap.parse_args(argv)
    use_llm = not args.no_llm
    max_eval = max(0, min(args.max_llm, MAX_LLM_HARD_CAP))

    now = datetime.now(JST)
    today = now.date()
    sources = load_json(CONFIG / "sources.json", [])
    profile = load_json(CONFIG / "profile.json", {})
    seen: dict = load_json(STATE / "seen.json", {})
    grants: dict = load_json(STATE / "grants.json", {})
    failures: dict = load_json(STATE / "failures.json", {})
    exit_code = 0

    def checkpoint() -> None:
        save_json(STATE / "seen.json", seen)
        save_json(STATE / "grants.json", grants)
        save_json(STATE / "failures.json", failures)

    client = None
    if use_llm and not args.collect_only:
        import anthropic
        client = anthropic.Anthropic()

    # 1. 収集
    candidates: list[tuple[fetch.RawItem, dict]] = []
    collected: list[dict] = []
    seen_urls_this_run: set[str] = set()
    stats = {"sources_ok": 0, "sources_failed": [], "items": 0, "new": 0, "prefiltered": 0, "evaluated": 0,
             "eval_failed": 0, "grants_added": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}}
    for src in sources:
        try:
            items = fetch.fetch_source(src)
            stats["sources_ok"] += 1
        except Exception as e:  # 1ソースの失敗で全体を止めない
            stats["sources_failed"].append(f"{src['id']}: {type(e).__name__}")
            print(f"[warn] {src['id']}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        stats["items"] += len(items)
        for it in items:
            row = {"source_id": src["id"], "source_name": src["name"], "title": it.title, "url": it.url,
                   "published": it.published, "public_ok": bool(src.get("public_ok", True)),
                   "prefilter": None, "decision": None, "fit_score": None}
            known_grant = grants.get(it.url)
            known_seen = seen.get(it.url)
            recheck = (known_grant and known_grant.get("status") == "closed"
                       and _age_days(known_grant.get("last_checked"), today) >= CLOSED_RECHECK_DAYS) or \
                      (known_seen and known_seen.get("reason") == "not_grant"
                       and _age_days(known_seen.get("first_seen"), today) >= SEEN_RECHECK_DAYS)
            if known_grant and not recheck:
                row.update(prefilter=True, decision="既知（助成）", fit_score=known_grant.get("fit_score"))
            elif known_seen and not recheck:
                is_pre = known_seen.get("reason") == "prefilter"
                row.update(prefilter=not is_pre, decision="既知（除外）" if is_pre else
                           ("既知（取得不可）" if known_seen.get("reason") == "fetch_failed" else "既知（助成ではない）"))
            elif it.url in seen_urls_this_run:
                row.update(decision="重複（同じ回で既出）")
            else:
                seen_urls_this_run.add(it.url)
                stats["new"] += 1
                passed = extract.prefilter(it, src, profile)
                row.update(prefilter=passed, decision=("再確認" if recheck else "判定へ") if passed else "規則で除外")
                if passed:
                    candidates.append((it, src))
                elif not args.collect_only:
                    seen[it.url] = {"first_seen": today.isoformat(), "is_grant": False, "reason": "prefilter"}
            collected.append(row)
    stats["prefiltered"] = len(candidates)
    print(f"収集: {stats['items']}件 / 新着 {stats['new']}件 / 判定対象 {len(candidates)}件 / 情報源の失敗 {len(stats['sources_failed'])}")
    if sources and stats["sources_ok"] == 0:
        print("[error] すべての情報源の取得に失敗", file=sys.stderr)
        exit_code = 1
    save_json(STATE / "collected" / f"{today.isoformat()}.json", collected)
    build.build_collected(collected, today, now, DOCS)
    if args.collect_only:
        print("収集のみ（判定・状態更新・通知はしない）")
        return exit_code

    # 2. 判定
    def row_for(url: str) -> dict | None:
        return next((c for c in collected if c["url"] == url), None)

    def note_failure(url: str, kind: str) -> None:
        f = failures.get(url, {"count": 0})
        f["count"] = f.get("count", 0) + 1
        f["last"] = today.isoformat()
        f["kind"] = kind
        failures[url] = f
        if f["count"] >= FAILURES_BEFORE_GIVE_UP:
            seen[url] = {"first_seen": today.isoformat(), "is_grant": False, "reason": "fetch_failed"}
            failures.pop(url, None)
            r = row_for(url)
            if r:
                r["decision"] = "取得不可（諦め）"

    ordered = _interleave(candidates)
    for i, (it, src) in enumerate(ordered):
        if i >= max_eval:
            print(f"判定の上限 {max_eval} 件に達したため、残り {len(ordered) - i} 件は次回に回す")
            break
        try:
            page_text = fetch.fetch_page_text(it.url)
        except fetch.FetchError as e:
            if "application/pdf" in str(e):
                page_text = "(PDF のため本文は取得していません。見出しと URL と一覧の要約だけで判断し、不明な項目は null にしてください)"
            else:
                print(f"[warn] 本文取得失敗（次回再試行）{it.url}: {e}", file=sys.stderr)
                note_failure(it.url, "fetch")
                stats["eval_failed"] += 1
                continue
        except Exception as e:
            print(f"[warn] 本文取得失敗（次回再試行）{it.url}: {type(e).__name__}", file=sys.stderr)
            note_failure(it.url, "fetch")
            stats["eval_failed"] += 1
            continue
        try:
            if use_llm:
                result, usage = extract.evaluate(client, it, page_text, profile, today)
                for k in stats["usage"]:
                    stats["usage"][k] += usage.get(k, 0)
            else:
                result = extract.rule_based(it, page_text, today)
            stats["evaluated"] += 1
        except Exception as e:
            print(f"[warn] 判定失敗（次回再試行）{it.url}: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc()
            note_failure(it.url, "evaluate")
            stats["eval_failed"] += 1
            continue
        failures.pop(it.url, None)
        row = row_for(it.url)
        if not result["is_grant_program"]:
            seen[it.url] = {"first_seen": today.isoformat(), "is_grant": False, "reason": "not_grant"}
            grants.pop(it.url, None)
            if row:
                row["decision"] = "助成ではない"
        else:
            previous = grants.get(it.url, {})
            record = dict(result)
            record.update({"url": it.url, "source_id": src["id"], "source_name": src["name"],
                           "public_ok": bool(src.get("public_ok", True)),
                           "first_seen": previous.get("first_seen") if previous and _age_days(previous.get("first_seen"), today) < CLOSED_RECHECK_DAYS else today.isoformat(),
                           "last_checked": today.isoformat()})
            grants[it.url] = record
            seen.pop(it.url, None)
            stats["grants_added"] += 1
            if row:
                row.update(decision="助成", fit_score=record["fit_score"])
            print(f"  + [{record['fit_score']:>3}] {record['title'][:70]}  締切 {record['deadline'] or '不明'}")
        if stats["evaluated"] % CHECKPOINT_EVERY == 0:
            checkpoint()
    if use_llm and candidates and stats["evaluated"] == 0 and max_eval > 0:
        print("[error] 判定対象があるのに1件も判定できなかった（API キーやモデル設定を確認）", file=sys.stderr)
        exit_code = 1

    # 3. 状態の更新（締切が過ぎたものは閉じる）
    for rec in grants.values():
        d = _days_left(rec.get("deadline"), today)
        rec["status"] = "closed" if d is not None and d < 0 else "open"
    checkpoint()
    save_json(STATE / "collected" / f"{today.isoformat()}.json", collected)

    # 4. 生成（同じ日に再実行しても「今週の新着」は first_seen が今日のもの全部になる）
    new_urls = {u for u, rec in grants.items() if rec.get("first_seen") == today.isoformat()}
    public_grants = [rec for rec in grants.values() if rec.get("public_ok", True)]
    report_path = build.build_all(public_grants, new_urls, today, now, DOCS, profile)
    build.build_collected(collected, today, now, DOCS)
    report_url = f"{pages_base_url()}/reports/{report_path.name}"
    stats["report_url"] = report_url
    save_json(STATE / "last_run.json", {"ran_at": now.isoformat(), "exit_code": exit_code, **stats})
    print(json.dumps(stats, ensure_ascii=False, indent=1))

    # 5. 通知
    if not args.no_mail:
        min_fit = profile.get("min_fit_public", 0)
        visible = [rec for rec in grants.values() if rec["status"] == "open" and rec.get("public_ok", True) and rec["fit_score"] >= min_fit]
        open_new = sorted((rec for rec in visible if rec["url"] in new_urls), key=lambda r: -r["fit_score"])
        closing = sorted((rec for rec in visible if rec["url"] not in new_urls and rec.get("deadline")
                          and 0 <= (_days_left(rec["deadline"], today) or -1) <= profile["closing_soon_days"]),
                         key=lambda r: r["deadline"])
        lines = [f"うきのわ 助成金の週報（{today.isoformat()}）", "",
                 f"今週の新着: {len(open_new)}件、締切が{profile['closing_soon_days']}日以内: {len(closing)}件", "",
                 f"週報ページ: {report_url}", ""]
        lines += [f"・[{rec['fit_score']}点] {rec['title']}（締切 {rec['deadline'] or '不明'}）" for rec in open_new[:10]]
        if closing:
            lines += ["", "締切が近いもの:"] + [f"・{rec['deadline']} {rec['title']}" for rec in closing[:10]]
        lines += ["", f"一覧: {pages_base_url()}/", f"カレンダー登録: {pages_base_url()}/subscribe.html"]
        if stats["sources_failed"] or stats["eval_failed"]:
            lines += ["", f"※ 取得できなかった情報源 {len(stats['sources_failed'])}件、判定を次回に回したページ {stats['eval_failed']}件。詳しくは収集ログを確認してください。"]
        text = "\n".join(lines)
        html_body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
        try:
            notify.send_report_mail(f"[うきのわ] 助成金の週報 {today.isoformat()}（新着{len(open_new)}件）", text, html_body)
        except Exception as e:
            print(f"[error] メール送信失敗: {type(e).__name__}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
