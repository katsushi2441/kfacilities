"""SQLite スキーマとヘルパ。

- facility : 施設（スポーツセンター・中学校体育館など）
- area     : 施設内の場所（第2競技場・トレーニング室・体育館…）と種目
- slot     : 日付×時間帯×面（板面/畳面）ごとの「個人利用できるか」
- geocode  : 住所→緯度経度のキャッシュ（地理院 API）
- meta     : 取り込み時刻など
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS facility (
  id TEXT PRIMARY KEY, region TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
  ward TEXT, address TEXT, postal TEXT, lat REAL, lng REAL, tel TEXT, url TEXT,
  hours TEXT, closed TEXT, fee TEXT, note TEXT, operator TEXT, station TEXT,
  sort_key INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS area (
  id TEXT PRIMARY KEY, facility_id TEXT NOT NULL, name TEXT NOT NULL,
  sports TEXT NOT NULL DEFAULT '[]', fee TEXT, note TEXT, source_page TEXT,
  FOREIGN KEY(facility_id) REFERENCES facility(id)
);
CREATE TABLE IF NOT EXISTS slot (
  area_id TEXT NOT NULL, date TEXT NOT NULL, slot TEXT NOT NULL, sub_area TEXT NOT NULL DEFAULT '',
  start TEXT, end TEXT, status TEXT NOT NULL, certainty TEXT NOT NULL DEFAULT '予定',
  sports TEXT, source_url TEXT, source_title TEXT, fetched_at TEXT, note TEXT,
  PRIMARY KEY(area_id, date, slot, sub_area)
);
CREATE INDEX IF NOT EXISTS idx_slot_date ON slot(date, status);
CREATE TABLE IF NOT EXISTS geocode (address TEXT PRIMARY KEY, lat REAL, lng REAL, title TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db_path() -> str:
    return os.environ.get("KFACILITIES_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "kfacilities.sqlite"))


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or db_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def tx(path: str | None = None):
    con = connect(path)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))


def get_meta(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default
