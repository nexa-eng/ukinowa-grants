"""情報源の取得。RSS と HTML の一覧ページに対応する。

守ること:
- 週1回だけ叩く。連絡先入りの UA を名乗る。robots.txt で拒否された URL は取りに行かない
- res.text を直接使わない（Shift_JIS の自治体サイトがある）。bytes から文字コードを判定して復号する
- 本文は AI 判定のためだけに取得し、保存しない。保存するのは要約と出典 URL
"""
from __future__ import annotations

import re
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

USER_AGENT = "UkinowaGrantsBot/0.1 (+https://github.com/nexa-eng/ukinowa-grants)"
TIMEOUT = 30
PAGE_TEXT_LIMIT = 12000

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


@dataclass
class RawItem:
    source_id: str
    source_name: str
    title: str
    url: str
    summary: str = ""
    published: str | None = None
    categories: list[str] = field(default_factory=list)


def _allowed_by_robots(url: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = requests.get(f"{base}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                _robots_cache[base] = rp
            else:
                _robots_cache[base] = None
        except requests.RequestException:
            _robots_cache[base] = None
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def get(url: str) -> requests.Response:
    if not _allowed_by_robots(url):
        raise PermissionError(f"robots.txt により拒否: {url}")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def decode(resp: requests.Response) -> str:
    """bytes から文字コードを判定して復号する。ヘッダの既定値 ISO-8859-1 は信用しない。"""
    declared = resp.encoding
    if declared and declared.lower() not in ("iso-8859-1", "latin-1"):
        try:
            return resp.content.decode(declared)
        except (UnicodeDecodeError, LookupError):
            pass
    for enc in (resp.apparent_encoding, "utf-8", "cp932", "euc_jp"):
        if not enc:
            continue
        try:
            return resp.content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return resp.content.decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)).strip()


def fetch_rss(src: dict) -> list[RawItem]:
    resp = get(src["url"])
    feed = feedparser.parse(resp.content)
    items: list[RawItem] = []
    for entry in feed.entries:
        link = entry.get("link")
        title = _strip_html(entry.get("title", ""))
        if not link or not title:
            continue
        items.append(RawItem(
            source_id=src["id"], source_name=src["name"], title=title, url=link,
            summary=_strip_html(entry.get("summary", ""))[:600],
            published=entry.get("published") or entry.get("updated"),
            categories=[t.get("term", "") for t in entry.get("tags", []) if t.get("term")],
        ))
    return items


def fetch_html_list(src: dict) -> list[RawItem]:
    """一覧ページのリンクを候補として拾う。選別は後段（規則 + AI）に任せる。"""
    resp = get(src["url"])
    soup = BeautifulSoup(decode(resp), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    base_host = urlparse(src["url"]).netloc
    seen: set[str] = set()
    items: list[RawItem] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if len(text) < 6:
            continue
        href = urljoin(src["url"], a["href"]).split("#")[0]
        if not href.startswith("http"):
            continue
        if src.get("same_domain") and urlparse(href).netloc != base_host:
            continue
        if href in seen or href.rstrip("/") == src["url"].rstrip("/"):
            continue
        seen.add(href)
        items.append(RawItem(source_id=src["id"], source_name=src["name"], title=text[:200], url=href))
    return items


def fetch_source(src: dict) -> list[RawItem]:
    if src["kind"] == "rss":
        return fetch_rss(src)
    if src["kind"] == "html":
        return fetch_html_list(src)
    raise ValueError(f"未対応の kind: {src['kind']}")


def fetch_page_text(url: str) -> str:
    """AI 判定用の本文テキスト。保存しない。"""
    resp = get(url)
    ctype = resp.headers.get("Content-Type", "")
    if "pdf" in ctype.lower() or url.lower().endswith(".pdf"):
        return "(PDF のため本文は取得していません。タイトルと URL から判断してください)"
    soup = BeautifulSoup(decode(resp), "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return text[:PAGE_TEXT_LIMIT]
