"""外部から来た値（第三者サイト・LLM 出力）を公開物に出す前の検証。1か所にまとめる。"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlparse

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TEXT = {"title": 200, "provider": 120, "summary": 600, "deadline_note": 120, "amount_note": 200,
            "fit_reason": 600, "region_scope": 20}
MAX_LIST_ITEMS = 10
MAX_LIST_ITEM_LEN = 40


def clean_text(s, limit: int = 600) -> str:
    """制御文字を除き、改行は空白に、長さを制限する。"""
    if not isinstance(s, str):
        return ""
    s = CONTROL.sub("", s).replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def safe_url(u) -> str | None:
    """http/https で、制御文字・空白を含まない URL だけ通す。"""
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u or CONTROL.search(u) or any(c in u for c in " \r\n\t"):
        return None
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return u


def _site(host: str) -> str:
    """ざっくりした登録ドメイン（末尾2〜3ラベル）。日本の .co.jp/.or.jp/.lg.jp/.go.jp 等に対応。"""
    parts = host.lower().split(".")
    if len(parts) >= 3 and parts[-1] == "jp" and len(parts[-2]) <= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_site(a: str, b: str) -> bool:
    try:
        return _site(urlparse(a).netloc) == _site(urlparse(b).netloc)
    except ValueError:
        return False


def valid_deadline(d, today: date) -> str | None:
    """YYYY-MM-DD で、過去3年〜未来2年の範囲だけ通す。"""
    if not isinstance(d, str) or not DATE_RE.match(d):
        return None
    try:
        dt = date.fromisoformat(d)
    except ValueError:
        return None
    if dt < today - timedelta(days=3 * 365) or dt > today + timedelta(days=2 * 365):
        return None
    return d


def sanitize_grant(result: dict, source_url: str, today: date) -> dict:
    """LLM の出力を公開してよい形に整える。apply_url は元ページと同じサイトのときだけ採用する。"""
    out = dict(result)
    for k, lim in MAX_TEXT.items():
        out[k] = clean_text(out.get(k, ""), lim)
    for k in ("eligible_types", "themes", "fit_programs"):
        v = out.get(k) or []
        out[k] = [clean_text(x, MAX_LIST_ITEM_LEN) for x in v if isinstance(x, str)][:MAX_LIST_ITEMS]
    out["fit_programs"] = [p for p in out["fit_programs"] if p in ("01", "02", "03", "04", "05")]
    try:
        out["fit_score"] = max(0, min(100, int(out.get("fit_score", 0))))
    except (TypeError, ValueError):
        out["fit_score"] = 0
    out["deadline"] = valid_deadline(out.get("deadline"), today)
    amt = out.get("amount_max_yen")
    out["amount_max_yen"] = int(amt) if isinstance(amt, (int, float)) and 0 <= amt <= 10**11 else None
    apply = safe_url(out.get("apply_url"))
    out["apply_url"] = apply if apply and same_site(apply, source_url) else source_url
    out["is_grant_program"] = bool(out.get("is_grant_program"))
    return out
