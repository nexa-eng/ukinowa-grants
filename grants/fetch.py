"""情報源の取得。RSS と HTML の一覧ページに対応する。

守ること:
- 週1回だけ叩き、同じホストには間隔を空ける。連絡先入りの UA を名乗る。robots.txt で拒否された URL は取りに行かない
- http/https 以外、内部アドレス（ループバック・プライベート・リンクローカル）には接続しない。リダイレクトも同じ検査を通す
- 本文は上限サイズまでしか読まず、HTML/XML 以外は読まない。res.text を直接使わず bytes から文字コードを判定する
- 本文は AI 判定のためだけに取得し、保存しない。保存するのは要約と出典 URL
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from .validate import safe_url

USER_AGENT = "UkinowaGrantsBot/0.1 (+https://github.com/nexa-eng/ukinowa-grants)"
TIMEOUT = 30
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
PAGE_TEXT_LIMIT = 12000
HOST_INTERVAL_SEC = 1.5
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/xml", "application/xml", "application/rss+xml",
                 "application/atom+xml", "application/rdf+xml", "text/plain")

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None | bool] = {}
_last_hit: dict[str, float] = {}


class FetchError(Exception):
    pass


@dataclass
class RawItem:
    source_id: str
    source_name: str
    title: str
    url: str
    summary: str = ""
    published: str | None = None
    categories: list[str] = field(default_factory=list)


def _is_public_host(host: str) -> bool:
    """DNS を引いて、すべてのアドレスがグローバルであることを確認する。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return False
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def _check_url(url: str) -> str:
    u = safe_url(url)
    if not u:
        raise FetchError(f"許可しない URL: {url[:80]}")
    host = urlparse(u).hostname or ""
    if not _is_public_host(host):
        raise FetchError(f"内部アドレスまたは解決不可のホスト: {host}")
    return u


def _throttle(host: str) -> None:
    last = _last_hit.get(host)
    if last is not None:
        wait = HOST_INTERVAL_SEC - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.monotonic()


def _allowed_by_robots(url: str) -> bool:
    """robots.txt。取得できない（5xx など）場合は RFC 9309 に従い拒否、404 は許可。"""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _robots_cache:
        try:
            _throttle(parsed.netloc)
            resp = requests.get(f"{base}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                                allow_redirects=False, stream=True)
            if resp.status_code == 200:
                body = resp.raw.read(200_000, decode_content=True).decode("utf-8", errors="replace")
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.splitlines())
                _robots_cache[base] = rp
            elif 400 <= resp.status_code < 500:
                _robots_cache[base] = None   # 無い → 許可
            else:
                _robots_cache[base] = False  # 5xx・リダイレクト → 拒否
        except requests.RequestException:
            _robots_cache[base] = False
    rp = _robots_cache[base]
    if rp is None:
        return True
    if rp is False:
        return False
    return rp.can_fetch(USER_AGENT, url)


def _read_limited(resp: requests.Response) -> bytes:
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > MAX_BYTES:
        raise FetchError(f"サイズ上限超過: {length} bytes")
    buf = bytearray()
    for chunk in resp.iter_content(65536):
        buf.extend(chunk)
        if len(buf) > MAX_BYTES:
            raise FetchError("サイズ上限超過")
    return bytes(buf)


def get(url: str) -> tuple[bytes, str, requests.Response]:
    """検査付きの取得。戻り値は (本文 bytes, 最終 URL, レスポンス)。リダイレクトは手動で追い、毎回検査する。"""
    current = _check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        if not _allowed_by_robots(current):
            raise FetchError(f"robots.txt により拒否: {current}")
        _throttle(urlparse(current).netloc)
        resp = requests.get(current, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                            allow_redirects=False, stream=True)
        if resp.is_redirect or resp.is_permanent_redirect:
            loc = resp.headers.get("Location")
            resp.close()
            if not loc:
                raise FetchError("Location のないリダイレクト")
            current = _check_url(urljoin(current, loc))
            continue
        if resp.status_code >= 400:
            resp.close()
            raise FetchError(f"HTTP {resp.status_code}")
        ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype and not any(ctype.startswith(t) for t in ALLOWED_TYPES):
            resp.close()
            raise FetchError(f"対象外の Content-Type: {ctype}")
        body = _read_limited(resp)
        resp.close()
        return body, current, resp
    raise FetchError("リダイレクトが多すぎる")


def decode(content: bytes, declared: str | None) -> str:
    """bytes から文字コードを判定して復号する。ヘッダの既定値 ISO-8859-1 は信用しない。"""
    if declared and declared.lower() not in ("iso-8859-1", "latin-1"):
        try:
            return content.decode(declared)
        except (UnicodeDecodeError, LookupError):
            pass
    m = re.search(rb'charset=["\']?([A-Za-z0-9_\-]+)', content[:4096])
    for enc in ((m.group(1).decode("ascii", "ignore") if m else None), "utf-8", "cp932", "euc_jp"):
        if not enc:
            continue
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)).strip()


def fetch_rss(src: dict) -> list[RawItem]:
    body, _, _ = get(src["url"])
    feed = feedparser.parse(body)
    items: list[RawItem] = []
    for entry in feed.entries:
        link = safe_url(entry.get("link"))
        title = _strip_html(entry.get("title", ""))[:200]
        if not link or not title:
            continue
        items.append(RawItem(
            source_id=src["id"], source_name=src["name"], title=title, url=link,
            summary=_strip_html(entry.get("summary", ""))[:600],
            published=str(entry.get("published") or entry.get("updated") or "")[:60] or None,
            categories=[str(t.get("term", ""))[:40] for t in entry.get("tags", []) if t.get("term")][:10],
        ))
    return items


def fetch_html_list(src: dict) -> list[RawItem]:
    """一覧ページのリンクを候補として拾う。選別は後段（規則 + AI）に任せる。"""
    body, final_url, resp = get(src["url"])
    soup = BeautifulSoup(decode(body, resp.encoding), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    base_host = urlparse(final_url).netloc
    seen: set[str] = set()
    items: list[RawItem] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if len(text) < 6:
            continue
        href = safe_url(urljoin(final_url, a["href"]).split("#")[0])
        if not href:
            continue
        if src.get("same_domain") and urlparse(href).netloc != base_host:
            continue
        if href in seen or href.rstrip("/") == final_url.rstrip("/"):
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
    """AI 判定用の本文テキスト。保存しない。PDF など HTML/XML 以外は読まない。"""
    body, _, resp = get(url)
    soup = BeautifulSoup(decode(body, resp.encoding), "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "template"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return text[:PAGE_TEXT_LIMIT]
