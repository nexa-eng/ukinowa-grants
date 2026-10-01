"""週次の実行入口。

python -m grants.run --llm                AI 判定あり（本番）
python -m grants.run --no-llm --no-mail   規則のみ（ローカル確認用）
python -m grants.run --llm --max-llm 20   AI 判定の件数を抑える
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
STATE = ROOT / "state"
DOCS = ROOT / "docs"
JST = timezone(timedelta(hours=9))


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--llm", action="store_true", help="Claude で判定する（本番）")
    g.add_argument("--no-llm", action="store_true", help="規則だけで判定する（確認用）")
    ap.add_argument("--max-llm", type=int, default=int(os.environ.get("MAX_LLM", "120")), help="1回の実行で判定する上限件数")
    ap.add_argument("--no-mail", action="store_true")
    args = ap.parse_args(argv)
    use_llm = not args.no_llm

    now = datetime.now(JST)
    today = now.date()
    sources = load_json(CONFIG / "sources.json", [])
    profile = load_json(CONFIG / "profile.json", {})
    seen: dict = load_json(STATE / "seen.json", {})
    grants: dict = load_json(STATE / "grants.json", {})

    client = None
    if use_llm:
        import anthropic
        client = anthropic.Anthropic()

    # 1. 収集
    candidates: list[tuple[fetch.RawItem, dict]] = []
    stats = {"sources_ok": 0, "sources_failed": [], "items": 0, "new": 0, "prefiltered": 0, "evaluated": 0,
             "grants_added": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}
    for src in sources:
        try:
            items = fetch.fetch_source(src)
            stats["sources_ok"] += 1
        except Exception as e:  # 1ソースの失敗で全体を止めない
            stats["sources_failed"].append(f"{src['id']}: {e}")
            print(f"[warn] {src['id']}: {e}", file=sys.stderr)
            continue
        stats["items"] += len(items)
        for it in items:
            if it.url in seen or it.url in grants:
                continue
            stats["new"] += 1
            if extract.prefilter(it, src, profile):
                candidates.append((it, src))
            else:
                seen[it.url] = {"first_seen": today.isoformat(), "is_grant": False, "reason": "prefilter"}
    stats["prefiltered"] = len(candidates)
    print(f"収集: {stats['items']}件 / 新着 {stats['new']}件 / 判定対象 {len(candidates)}件")

    # 2. 判定
    new_urls: set[str] = set()
    for i, (it, src) in enumerate(candidates):
        if i >= args.max_llm:
            print(f"判定の上限 {args.max_llm} 件に達したため、残り {len(candidates) - i} 件は次回に回す")
            break
        try:
            page_text = fetch.fetch_page_text(it.url)
        except Exception as e:
            print(f"[warn] 本文取得失敗 {it.url}: {e}", file=sys.stderr)
            page_text = f"(本文を取得できませんでした: {e})"
        try:
            if use_llm:
                result, usage = extract.evaluate(client, it, page_text, profile, today)
                stats["usage"]["input_tokens"] += usage["input_tokens"]
                stats["usage"]["output_tokens"] += usage["output_tokens"]
            else:
                result = extract.rule_based(it, page_text, today)
            stats["evaluated"] += 1
        except Exception as e:
            print(f"[warn] 判定失敗 {it.url}: {e}", file=sys.stderr)
            traceback.print_exc()
            continue  # 次回また試す（seen に入れない）
        if not result["is_grant_program"]:
            seen[it.url] = {"first_seen": today.isoformat(), "is_grant": False, "reason": "not_grant"}
            continue
        record = dict(result)
        record.update({"url": it.url, "source_id": src["id"], "source_name": src["name"],
                       "public_ok": bool(src.get("public_ok", True)), "first_seen": today.isoformat(),
                       "last_checked": today.isoformat()})
        grants[it.url] = record
        new_urls.add(it.url)
        stats["grants_added"] += 1
        print(f"  + [{record['fit_score']:>3}] {record['title']}  締切 {record['deadline'] or '不明'}")

    # 3. 状態の更新（締切が過ぎたものは閉じる）
    for rec in grants.values():
        d = _days_left(rec.get("deadline"), today)
        rec["status"] = "closed" if d is not None and d < 0 else "open"
    save_json(STATE / "seen.json", seen)
    save_json(STATE / "grants.json", grants)

    # 4. 生成
    public_grants = [rec for rec in grants.values() if rec.get("public_ok", True)]
    report_path = build.build_all(public_grants, new_urls, today, now, DOCS, profile)
    report_url = f"{pages_base_url()}/reports/{report_path.name}"
    stats["report_url"] = report_url
    save_json(STATE / "last_run.json", {"ran_at": now.isoformat(), **stats})
    print(json.dumps(stats, ensure_ascii=False, indent=1))

    # 5. 通知
    if not args.no_mail:
        open_new = sorted((grants[u] for u in new_urls if grants[u]["status"] == "open"), key=lambda r: -r["fit_score"])
        closing = sorted(
            (rec for rec in grants.values() if rec["status"] == "open" and rec["url"] not in new_urls
             and (dl := _days_left(rec.get("deadline"), today)) is not None and 0 <= dl <= profile["closing_soon_days"]),
            key=lambda r: r["deadline"])
        lines = [f"うきのわ 助成金の週報（{today.isoformat()}）", "",
                 f"今週の新着: {len(open_new)}件、締切が{profile['closing_soon_days']}日以内: {len(closing)}件", "",
                 f"週報ページ: {report_url}", ""]
        for rec in open_new[:10]:
            lines.append(f"・[{rec['fit_score']}点] {rec['title']}（締切 {rec['deadline'] or '不明'}）")
        if closing:
            lines += ["", "締切が近いもの:"] + [f"・{rec['deadline']} {rec['title']}" for rec in closing[:10]]
        lines += ["", f"一覧: {pages_base_url()}/", f"カレンダー購読: {pages_base_url()}/grants.ics"]
        text = "\n".join(lines)
        html_body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
        try:
            notify.send_report_mail(f"[うきのわ] 助成金の週報 {today.isoformat()}（新着{len(open_new)}件）", text, html_body)
        except Exception as e:
            print(f"[warn] メール送信失敗: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
