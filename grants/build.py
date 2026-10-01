"""公開ページ・週報・カレンダー・一覧データの生成（docs/ 配下）。"""
from __future__ import annotations

import hashlib
import html
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .validate import CONTROL, safe_url

PROGRAM_LABELS = {"01": "震災復興支援", "02": "寺院BCP", "03": "TOYONO-VA", "04": "移住・空き家", "05": "里山体験"}

CSS = """
:root{--bg:#fbfaf6;--surface:#fdfcf9;--sunken:#f5f3ee;--ink:#1c1f25;--text:#34383e;--muted:#62666d;
--primary:#009988;--wash:#d6f7f1;--accent-ink:#02544c;--line:#d9d7d2;--warn-bg:#fff3d6;--warn-fg:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#15181d;--surface:#1d2127;--sunken:#121519;--ink:#f1f1ee;--text:#d7d8d4;--muted:#9aa0a8;
--primary:#2fc2ae;--wash:#133c37;--accent-ink:#8fe3d6;--line:#2f343c;--warn-bg:#3d3015;--warn-fg:#f1c766}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:"Hiragino Kaku Gothic ProN","Hiragino Sans","Noto Sans JP",-apple-system,sans-serif;font-size:15px;line-height:1.75;line-break:strict;overflow-wrap:anywhere}
.wrap{max-width:960px;margin:0 auto;padding:16px 20px 64px}h1{font-family:"Hiragino Mincho ProN","Noto Serif JP",serif;font-size:26px;color:var(--ink);margin:12px 0 4px}
h2{font-size:19px;color:var(--ink);margin:28px 0 10px;border-left:4px solid var(--primary);padding-left:10px}
.kicker{font-size:12px;letter-spacing:.16em;color:var(--accent-ink);font-weight:700}.muted{color:var(--muted);font-size:13px}
nav a{margin-right:14px;color:var(--accent-ink)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:10px 0}
.card h3{margin:0 0 6px;font-size:16px;color:var(--ink)}.card h3 a{color:var(--ink);text-decoration:none}.card h3 a:hover{text-decoration:underline}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px;color:var(--muted)}
.chip{display:inline-block;font-size:11.5px;padding:1px 9px;border-radius:4px;font-weight:700}
.chip.ok{background:var(--wash);color:var(--accent-ink)}.chip.warn{background:var(--warn-bg);color:var(--warn-fg)}.chip.p{background:var(--sunken);color:var(--text);border:1px solid var(--line)}
.score{font-weight:700;color:var(--primary)}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.filters button{border:1px solid var(--line);background:var(--surface);color:var(--text);padding:5px 12px;border-radius:999px;cursor:pointer;font-size:13px}
.filters button[aria-pressed="true"]{background:var(--primary);color:#fff;border-color:var(--primary)}
.foot{margin-top:40px;padding-top:14px;border-top:1px solid var(--line)}.foot p{margin:4px 0}.foot a{color:var(--accent-ink)}
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _yen(n) -> str:
    if n is None:
        return "—"
    if n >= 10000 and n % 10000 == 0:
        return f"{n // 10000:,}万円"
    return f"{n:,}円"


def _days_left(deadline: str | None, today: date) -> int | None:
    if not deadline:
        return None
    try:
        return (date.fromisoformat(deadline) - today).days
    except ValueError:
        return None


def _score(g: dict) -> int:
    try:
        return max(0, min(100, int(g.get("fit_score", 0))))
    except (TypeError, ValueError):
        return 0


def _deadline_chip(g: dict, today: date) -> str:
    d = _days_left(g.get("deadline"), today)
    if d is None:
        return f'<span class="chip p">{_esc(g.get("deadline_note") or "締切不明")}</span>'
    if d < 0:
        return '<span class="chip p">締切済み</span>'
    cls = "warn" if d <= 21 else "ok"
    return f'<span class="chip {cls}">締切 {_esc(g["deadline"])}（あと{d}日）</span>'


def _card(g: dict, today: date) -> str:
    progs = " ".join(f'<span class="chip p">{_esc(PROGRAM_LABELS.get(p, p))}</span>' for p in g.get("fit_programs", []))
    link = safe_url(g.get("apply_url")) or safe_url(g.get("url")) or ""
    title_html = f'<a href="{_esc(link)}" target="_blank" rel="noopener">{_esc(g["title"])}</a>' if link else _esc(g["title"])
    return f"""<div class="card" data-programs="{_esc(' '.join(g.get('fit_programs', [])))}">
  <h3>{title_html}</h3>
  <div class="row">{_deadline_chip(g, today)} <span class="score">合う度 {_score(g)}</span> <span>上限 {_yen(g.get('amount_max_yen'))}</span> <span>{_esc(g.get('provider', ''))}</span> <span>{_esc(g.get('region_scope', ''))}</span></div>
  <p style="margin:8px 0 4px">{_esc(g.get('summary', ''))}</p>
  <p class="muted" style="margin:0 0 6px">{_esc(g.get('fit_reason', ''))}</p>
  <div class="row">{progs} <span>対象: {_esc('、'.join(g.get('eligible_types', [])) or '不明')}</span> <span>出所: {_esc(g.get('source_name', ''))}</span></div>
</div>"""


def _page(title: str, body: str, generated: datetime, depth: int = 0) -> str:
    rel = "../" * depth
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{_esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<p class="kicker">うきのわ 助成金カレンダー</p>
<nav><a href="{rel}index.html">募集中の一覧</a><a href="{rel}reports/index.html">週報</a><a href="{rel}collected/index.html">収集ログ</a><a href="{rel}subscribe.html">カレンダー登録</a><a href="{rel}grants.json">データ（JSON）</a></nav>
{body}
<footer class="foot">
<p class="muted">生成: {generated.strftime('%Y-%m-%d %H:%M')} JST。情報源の公開情報を自動収集し AI が整理したものです。応募の可否・締切・条件は必ず公式ページで確認してください。助成情報の権利は、それぞれの財団・自治体・団体にあります。</p>
<p class="muted">&copy; 2026 <a href="https://nexa-eng.com/">Nexa Engineering株式会社</a>（企画・開発・運用）。仕組みは <a href="https://github.com/nexa-eng/ukinowa-grants">MIT ライセンス（GitHub）</a>、このページのデータは <a href="https://creativecommons.org/licenses/by/4.0/deed.ja">CC BY 4.0</a>（出典「うきのわ 助成金カレンダー（Nexa Engineering）」の表示で転載・加工可）。「うきのわ」の名称は団体のものです。</p>
</footer>
</div>
<script>
document.querySelectorAll('.filters button').forEach(b=>b.addEventListener('click',()=>{{
  const p=b.dataset.p;document.querySelectorAll('.filters button').forEach(x=>x.setAttribute('aria-pressed',x===b));
  document.querySelectorAll('.card[data-programs]').forEach(c=>{{c.style.display=(p==='all'||c.dataset.programs.split(' ').includes(p))?'':'none'}});
}}));
</script></body></html>"""


def build_index(grants: list[dict], today: date, now: datetime, out: Path) -> None:
    open_grants = sorted([g for g in grants if g.get("status") == "open"],
                         key=lambda g: (g.get("deadline") or "9999-12-31", -g.get("fit_score", 0)))
    filters = '<div class="filters"><button data-p="all" aria-pressed="true">すべて</button>' + "".join(
        f'<button data-p="{k}" aria-pressed="false">{v}</button>' for k, v in PROGRAM_LABELS.items()) + "</div>"
    cards = "\n".join(_card(g, today) for g in open_grants) or '<p class="muted">募集中の助成はまだ登録されていません。</p>'
    body = f"<h1>募集中の助成金（{len(open_grants)}件）</h1><p class='muted'>締切が近い順。合う度は、うきのわが応募できて事業に合う度合いの目安です（100点満点）。</p>{filters}{cards}"
    (out / "index.html").write_text(_page("うきのわ 助成金カレンダー", body, now), encoding="utf-8")


def build_report(grants: list[dict], new_urls: set[str], today: date, now: datetime, out: Path, closing_days: int) -> Path:
    new_items = sorted([g for g in grants if g["url"] in new_urls and g.get("status") == "open"], key=lambda g: -g.get("fit_score", 0))
    closing = sorted(
        [g for g in grants if g.get("status") == "open" and g["url"] not in new_urls
         and (d := _days_left(g.get("deadline"), today)) is not None and 0 <= d <= closing_days],
        key=lambda g: g["deadline"])
    body = f"<h1>週報 {today.isoformat()}</h1>"
    body += f"<h2>今週の新着（{len(new_items)}件）</h2>" + ("\n".join(_card(g, today) for g in new_items) or "<p class='muted'>新着はありませんでした。</p>")
    body += f"<h2>締切が{closing_days}日以内（{len(closing)}件）</h2>" + ("\n".join(_card(g, today) for g in closing) or "<p class='muted'>該当なし。</p>")
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{today.isoformat()}.html"
    path.write_text(_page(f"週報 {today.isoformat()}", body, now, depth=1), encoding="utf-8")
    files = sorted((f for f in reports.glob("*.html") if f.name != "index.html"), reverse=True)
    links = "\n".join(f'<li><a href="./{f.name}">{f.stem}</a></li>' for f in files)
    (reports / "index.html").write_text(_page("週報一覧", f"<h1>週報一覧</h1><ul>{links}</ul>", now, depth=1), encoding="utf-8")
    return path


def build_json(grants: list[dict], out: Path) -> None:
    (out / "grants.json").write_text(json.dumps([g for g in grants if g.get("status") == "open"], ensure_ascii=False, indent=1), encoding="utf-8")


def _ics_escape(s: str) -> str:
    s = CONTROL.sub("", str(s)).replace("\r", "")
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    """RFC 5545: 1行は75オクテットまで。超える分は CRLF + 空白で続ける。"""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 75:
            out.append(cur)
            cur, size = " " + ch, 1 + n
        else:
            cur, size = cur + ch, size + n
    out.append(cur)
    return "\r\n".join(out)


def build_ics(grants: list[dict], today: date, now: datetime, out: Path, alarm_days: list[int]) -> None:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ukinowa//grants//JA", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:うきのわ 助成金の締切", "X-WR-TIMEZONE:Asia/Tokyo"]
    # DTSTAMP は RFC 5545 で UTC（末尾 Z）必須。Google 経由の取り込みはここが厳しい
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for g in grants:
        if g.get("status") != "open" or not g.get("deadline"):
            continue
        try:
            d = date.fromisoformat(g["deadline"])
        except ValueError:
            continue
        uid_base = hashlib.sha1(g["url"].encode("utf-8")).hexdigest()[:16]
        link = safe_url(g.get("apply_url")) or safe_url(g.get("url")) or ""
        desc = _ics_escape(f"{g.get('summary', '')}\n上限: {_yen(g.get('amount_max_yen'))}\n{link}")
        events = [(d, f"締切: {g['title']}")] + [(d - timedelta(days=n), f"締切{n}日前: {g['title']}") for n in alarm_days]
        for i, (day, summary) in enumerate(events):
            if day < today:
                continue
            lines += ["BEGIN:VEVENT", f"UID:{uid_base}-{i}@ukinowa-grants", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
                      f"SUMMARY:{_ics_escape(summary)}", f"DESCRIPTION:{desc}", "TRANSP:TRANSPARENT"] + ([f"URL:{link}"] if link else []) + ["END:VEVENT"]
    lines.append("END:VCALENDAR")
    (out / "grants.ics").write_text("\r\n".join(_ics_fold(l) for l in lines) + "\r\n", encoding="utf-8")


def build_all(grants: list[dict], new_urls: set[str], today: date, now: datetime, out: Path, profile: dict) -> Path:
    # 合う度が低いものは公開しない（state には残す）
    grants = [g for g in grants if g.get("fit_score", 0) >= profile.get("min_fit_public", 0)]
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").touch()
    build_index(grants, today, now, out)
    report = build_report(grants, new_urls, today, now, out, profile["closing_soon_days"])
    build_json(grants, out)
    build_ics(grants, today, now, out, profile["alarm_days_before"])
    build_subscribe(out, now, profile.get("pages_base_url", "https://nexa-eng.github.io/ukinowa-grants"))
    return report


def build_collected(collected: list[dict], today: date, now: datetime, out: Path) -> None:
    """判定前の全件（収集ログ）。見逃しの点検に使う。"""
    d = out / "collected"
    d.mkdir(parents=True, exist_ok=True)
    order = {"助成": 0, "既知（助成）": 1, "判定へ": 2, "助成ではない": 3, "既知（助成ではない）": 4, "規則で除外": 5, "既知（除外）": 6}
    rows = sorted((r for r in collected if r.get("public_ok", True)),
                  key=lambda r: (order.get(r.get("decision") or "", 9), r["source_name"], r["title"]))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("decision") or "不明"] = counts.get(r.get("decision") or "不明", 0) + 1
    summary = " ".join(f'<span class="chip p">{_esc(k)} {v}</span>' for k, v in counts.items())
    trs = "\n".join(
        f"<tr><td>{_esc(r['source_name'])}</td><td>{(f'<a href=\"{_esc(safe_url(r['url']))}\" target=\"_blank\" rel=\"noopener\">{_esc(r['title'])}</a>' if safe_url(r['url']) else _esc(r['title']))}</td>"
        f"<td class=\"dec\">{_esc(r.get('decision') or '')}</td><td class=\"sc\">{'' if r.get('fit_score') is None else r['fit_score']}</td></tr>"
        for r in rows)
    body = (f"<h1>収集ログ {today.isoformat()}（{len(rows)}件）</h1>"
            f"<p class='muted'>情報源から拾った全件と、規則と AI の判断。「知っていた助成が出ていない」を見つけるための一覧です。</p>"
            f"<div class='row' style='margin:10px 0'>{summary}</div>"
            f"<div class='tbl'><table><thead><tr><th style='min-width:9em'>情報源</th><th>見出し</th><th class='dec'>判断</th><th class='sc'>合う度</th></tr></thead><tbody>{trs}</tbody></table></div>")
    (d / f"{today.isoformat()}.html").write_text(_page(f"収集ログ {today.isoformat()}", body, now, depth=1), encoding="utf-8")
    files = sorted((f for f in d.glob("*.html") if f.name != "index.html"), reverse=True)
    links = "\n".join(f'<li><a href="./{f.name}">{f.stem}</a></li>' for f in files)
    (d / "index.html").write_text(_page("収集ログ一覧", f"<h1>収集ログ一覧</h1><ul>{links}</ul>", now, depth=1), encoding="utf-8")


def build_subscribe(out: Path, now: datetime, base_url: str) -> None:
    """カレンダー購読の案内ページ。取り込み（コピー）ではなく購読（自動更新）を勧める。"""
    ics_https = f"{base_url}/grants.ics"
    ics_webcal = ics_https.replace("https://", "webcal://", 1)
    body = f"""<h1>カレンダー登録</h1>
<p>「購読」にすると、毎週月曜の更新が自動で手元のカレンダーに反映されます。ファイルを開いて取り込む方法はその時点のコピーで、以後は更新されません。</p>
<h2>Google カレンダー（パソコンの Web 版で1回だけ設定）</h2>
<ol><li>左の「他のカレンダー」の「+」→「URL で追加」</li><li>次の URL を貼る: <code>{_esc(ics_https)}</code></li><li>「カレンダーを追加」。スマホの Google カレンダーにも自動で出ます</li></ol>
<h2>iPhone・iPad</h2>
<ol><li>設定 → カレンダー → アカウント → アカウントを追加 → その他 → 照会カレンダーを追加</li><li>サーバーに次の URL を貼る: <code>{_esc(ics_https)}</code></li></ol>
<h2>Mac のカレンダー</h2>
<ol><li>ファイル → 新規照会カレンダー に次の URL を貼る: <code>{_esc(ics_https)}</code></li><li>または <a href="{_esc(ics_webcal)}">このリンク（webcal）</a> を開く</li></ol>
<p class="muted">「ファイル → 読み込む」で Google のカレンダーに入れようとするとエラーになることがあります。上の「照会」を使ってください。</p>
<h2>内容</h2>
<p>締切の当日と、21日前・7日前に終日の予定として出ます。締切が過ぎたものは次の更新で消えます。</p>"""
    (out / "subscribe.html").write_text(_page("カレンダー登録", body, now), encoding="utf-8")
