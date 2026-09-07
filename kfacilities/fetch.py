"""公開ページ・PDF の取得（キャッシュ付き）と、ページ内の PDF リンク発見。

- 取得物は outputs/raw/<sha1>.<ext> に保存し、同じ URL は TTL 内なら再取得しない。
- 取り込み元には負荷をかけない（1 リクエストごとに間隔、UA 明示）。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import urllib.parse

import requests

UA = "kfacilities/0.1 (+https://kurage.exbridge.jp/) facility-schedule-index"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "outputs", "raw")
_last = 0.0


def _polite():
    global _last
    wait = 1.0 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()


def get(url: str, ttl_hours: float = 6, binary: bool = False) -> tuple[bytes, str]:
    """(content, cached_path)"""
    os.makedirs(RAW, exist_ok=True)
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    ext = ".pdf" if binary or url.lower().endswith(".pdf") else ".html"
    path = os.path.join(RAW, h + ext)
    meta = path + ".json"
    if os.path.exists(path) and os.path.exists(meta):
        m = json.load(open(meta))
        if time.time() - m.get("fetched", 0) < ttl_hours * 3600:
            return open(path, "rb").read(), path
    _polite()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=40)
    r.raise_for_status()
    open(path, "wb").write(r.content)
    json.dump({"url": url, "fetched": time.time(), "status": r.status_code, "ctype": r.headers.get("Content-Type", "")}, open(meta, "w"))
    return r.content, path


def find_links(page_url: str, pattern: str, ttl_hours: float = 6, text_pattern: str | None = None) -> list[dict]:
    """ページ内の <a href> から pattern（正規表現、href 対象）に合うものを返す。"""
    content, _ = get(page_url, ttl_hours)
    doc = content.decode("utf-8", "replace")
    out = []
    seen = set()
    for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', doc, re.S | re.I):
        href = html.unescape(m.group(1))
        url = urllib.parse.urljoin(page_url, href)
        title = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        title = re.sub(r"\s+", " ", title)
        if re.search(pattern, url) and (not text_pattern or re.search(text_pattern, title)):
            if url not in seen:
                seen.add(url)
                out.append({"url": url, "title": title})
    return out


def find_pdfs_in_page(page_url: str, ttl_hours: float = 6) -> list[dict]:
    return find_links(page_url, r"\.pdf(\?|$)", ttl_hours)
