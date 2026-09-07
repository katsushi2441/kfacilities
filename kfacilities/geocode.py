"""住所→緯度経度（国土地理院 AddressSearch API、キー不要）。DB にキャッシュする。

施設名だと誤ヒットするので、必ず住所文字列を渡す（kshoken の教訓）。
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse

import requests

API = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="


def lookup(con, address: str) -> tuple[float, float] | None:
    if not address:
        return None
    row = con.execute("SELECT lat,lng FROM geocode WHERE address=?", (address,)).fetchone()
    if row:
        return (row["lat"], row["lng"]) if row["lat"] is not None else None
    time.sleep(1.0)
    try:
        r = requests.get(API + urllib.parse.quote(address), timeout=20, headers={"User-Agent": "kfacilities/0.1"})
        items = r.json()
    except Exception:
        items = []
    best = None
    for it in items:
        title = it.get("properties", {}).get("title", "")
        lng, lat = it["geometry"]["coordinates"]
        # 市区町村までしか一致しない候補より、町名まで一致する候補を優先
        score = sum(1 for ch in title if ch in address)
        if best is None or score > best[0]:
            best = (score, lat, lng, title)
    con.execute(
        "INSERT OR REPLACE INTO geocode(address,lat,lng,title,fetched_at) VALUES(?,?,?,?,?)",
        (address, best[1] if best else None, best[2] if best else None, best[3] if best else None, dt.datetime.now().isoformat(timespec="seconds")),
    )
    return (best[1], best[2]) if best else None
