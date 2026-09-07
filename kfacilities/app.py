"""Kurage 施設検索 — Web/API（FastAPI）。

  .venv/bin/uvicorn kfacilities.app:app --host 127.0.0.1 --port 18384

公開は kurage.exbridge.jp/kfacilities.php/ の透過プロキシ経由（相対パスで動く）。
検索は SQLite の slot 表を引くだけで、LLM は使わない。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
from collections import OrderedDict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from . import db as dbm

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC_BASE = os.environ.get("KFACILITIES_PUBLIC_BASE", "https://kurage.exbridge.jp/kfacilities.php/").rstrip("/") + "/"
SITE_NAME = "Kurage 施設検索"
WDAY_JA = "月火水木金土日"
TIME_FILTERS = OrderedDict([("", "いつでも"), ("am", "午前（〜12時）"), ("pm", "午後（12〜18時）"), ("night", "夜間（18時〜）")])
KIND_LABEL = {"center": "スポーツセンター", "school": "中学校体育館（地域スポーツセンター）"}
STATUS_MARK = {"open": "○", "partial": "△", "closed": "×", "holiday": "休", "na": "－", "unknown": "?"}

app = FastAPI(title=SITE_NAME, version=__version__, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def root_prefix(request: Request) -> str:
    """相対パス用のプレフィックス。/facility/x → ../ 、/ や /about → 空。"""
    segs = [s for s in request.url.path.split("/") if s]
    depth = max(0, len(segs) - 1)
    return "../" * depth


def jdate(d: dt.date) -> str:
    return f"{d.month}月{d.day}日（{WDAY_JA[d.weekday()]}）"


templates.env.globals.update(site_name=SITE_NAME, public_base=PUBLIC_BASE, version=__version__, jdate=jdate, time_filters=TIME_FILTERS, kind_label=KIND_LABEL, status_mark=STATUS_MARK)


def parse_date(s: str | None) -> dt.date:
    today = dt.date.today()
    if not s or s == "today":
        return today
    if s == "tomorrow":
        return today + dt.timedelta(days=1)
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return today


def haversine(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def time_ok(start: str | None, end: str | None, t: str) -> bool:
    if not t:
        return True
    s, e = start or "00:00", end or "24:00"
    if t == "am":
        return s < "12:00"
    if t == "pm":
        return s < "18:00" and e > "12:00"
    if t == "night":
        return e > "18:00"
    if re.fullmatch(r"\d{2}:\d{2}", t):  # 「18:30以降」のような指定
        return e > t
    return True


def search(date: dt.date, t: str = "", sport: str = "", ward: str = "", kind: str = "", lat: float | None = None, lng: float | None = None, radius_km: float | None = None, limit: int = 200) -> dict:
    con = dbm.connect()
    try:
        rows = con.execute(
            """SELECT f.id fid, f.name fname, f.kind, f.ward, f.address, f.lat, f.lng, f.tel, f.url, f.hours, f.closed, f.fee ffee, f.note fnote, f.station,
                      a.id aid, a.name aname, a.sports asports, a.fee afee, a.note anote,
                      s.slot, s.sub_area, s.start, s.end, s.status, s.certainty, s.sports ssports, s.source_url, s.source_title, s.fetched_at, s.note snote
               FROM slot s JOIN area a ON a.id=s.area_id JOIN facility f ON f.id=a.facility_id
               WHERE s.date=? AND s.status IN ('open','partial') ORDER BY f.sort_key, f.id, a.name, s.start, s.sub_area""",
            (date.isoformat(),),
        ).fetchall()
        last = dbm.get_meta(con, "last_ingest", {}) or {}
        region_name = dbm.get_meta(con, "region_name", "")
    finally:
        con.close()
    facs: OrderedDict[str, dict] = OrderedDict()
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        if ward and r["ward"] != ward:
            continue
        if not time_ok(r["start"], r["end"], t):
            continue
        sports = json.loads(r["ssports"] or "[]") or json.loads(r["asports"] or "[]")
        if sport and not any(sport in sp for sp in sports):
            continue
        f = facs.get(r["fid"])
        if not f:
            dist = None
            if lat is not None and lng is not None and r["lat"] is not None:
                dist = haversine(lat, lng, r["lat"], r["lng"])
            if radius_km and dist is not None and dist > radius_km:
                continue
            f = facs[r["fid"]] = {
                "id": r["fid"], "name": r["fname"], "kind": r["kind"], "ward": r["ward"], "address": r["address"], "lat": r["lat"], "lng": r["lng"],
                "tel": r["tel"], "url": r["url"], "hours": r["hours"], "closed": r["closed"], "fee": r["ffee"], "note": r["fnote"], "station": r["station"],
                "distance_km": round(dist, 1) if dist is not None else None, "areas": OrderedDict(),
            }
        a = f["areas"].get(r["aid"])
        if not a:
            a = f["areas"][r["aid"]] = {"name": r["aname"], "sports": json.loads(r["asports"] or "[]"), "fee": r["afee"], "note": r["anote"], "slots": [], "source_url": r["source_url"], "source_title": r["source_title"], "fetched_at": r["fetched_at"]}
        a["slots"].append({"slot": r["slot"], "sub_area": r["sub_area"], "start": r["start"], "end": r["end"], "status": r["status"], "certainty": r["certainty"], "sports": sports, "note": r["snote"]})
    items = list(facs.values())
    if lat is not None and lng is not None:
        items.sort(key=lambda x: (x["distance_km"] is None, x["distance_km"] or 0))
    for f in items:
        f["areas"] = list(f["areas"].values())
    return {"date": date.isoformat(), "date_label": jdate(date), "count": len(items), "items": items[:limit], "region": region_name, "last_ingest": last.get("finished")}


def facets() -> dict:
    con = dbm.connect()
    try:
        wards = [r[0] for r in con.execute("SELECT DISTINCT ward FROM facility WHERE ward IS NOT NULL ORDER BY sort_key, ward").fetchall()]
        sports: dict[str, int] = {}
        for (js,) in con.execute("SELECT sports FROM area").fetchall():
            for s in json.loads(js or "[]"):
                sports[s] = sports.get(s, 0) + 1
        n_center = con.execute("SELECT COUNT(*) FROM facility WHERE kind='center'").fetchone()[0]
        n_school = con.execute("SELECT COUNT(*) FROM facility WHERE kind='school'").fetchone()[0]
        last = dbm.get_meta(con, "last_ingest", {}) or {}
    finally:
        con.close()
    top = sorted(sports.items(), key=lambda kv: -kv[1])
    order = ["バドミントン", "卓球", "バスケットボール", "バレーボール", "武道", "ダンス", "ヨガ", "トレーニング", "水泳"]
    names = [s for s in order if s in sports] + [s for s, _ in top if s not in order][:8]
    wards_sorted = sorted(set(wards), key=lambda w: (w not in NAGOYA_WARDS, NAGOYA_WARDS.index(w) if w in NAGOYA_WARDS else 0))
    return {"wards": wards_sorted, "sports": names, "n_center": n_center, "n_school": n_school, "last_ingest": last.get("finished")}


NAGOYA_WARDS = ["千種区", "東区", "北区", "西区", "中村区", "中区", "昭和区", "瑞穂区", "熱田区", "中川区", "港区", "南区", "守山区", "緑区", "名東区", "天白区"]


@app.get("/", response_class=HTMLResponse)
def index(request: Request, date: str = "today", t: str = "", sport: str = "", ward: str = "", kind: str = "", lat: float | None = None, lng: float | None = None):
    d = parse_date(date)
    res = search(d, t, sport, ward, kind, lat, lng)
    fx = facets()
    q = {"date": date if date in ("today", "tomorrow") else d.isoformat(), "t": t, "sport": sport, "ward": ward, "kind": kind, "lat": lat, "lng": lng}
    return templates.TemplateResponse(request, "index.html", {"root": root_prefix(request), "res": res, "fx": fx, "q": q, "today": dt.date.today(), "path": ""})


@app.get("/api/search")
def api_search(date: str = "today", t: str = "", sport: str = "", ward: str = "", kind: str = "", lat: float | None = None, lng: float | None = None, radius_km: float | None = None):
    return JSONResponse(search(parse_date(date), t, sport, ward, kind, lat, lng, radius_km))


@app.get("/api/facilities")
def api_facilities():
    con = dbm.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT id,name,kind,ward,address,lat,lng,tel,url,hours,closed,fee FROM facility ORDER BY sort_key").fetchall()]
    finally:
        con.close()
    return JSONResponse({"count": len(rows), "items": rows})


def facility_detail(fid: str, days: int = 14) -> dict | None:
    con = dbm.connect()
    try:
        f = con.execute("SELECT * FROM facility WHERE id=?", (fid,)).fetchone()
        if not f:
            return None
        f = dict(f)
        today = dt.date.today()
        end = today + dt.timedelta(days=days)
        areas = []
        for a in con.execute("SELECT * FROM area WHERE facility_id=? ORDER BY name", (fid,)).fetchall():
            a = dict(a)
            a["sports"] = json.loads(a["sports"] or "[]")
            slots = [dict(s) for s in con.execute("SELECT * FROM slot WHERE area_id=? AND date>=? AND date<=? ORDER BY date, start, sub_area", (a["id"], today.isoformat(), end.isoformat())).fetchall()]
            cols = []
            for s in slots:
                key = (s["slot"], s["sub_area"], s["start"], s["end"])
                if key not in cols:
                    cols.append(key)
            cols.sort(key=lambda k: (k[2] or "", k[0], k[1]))
            grid = OrderedDict()
            for i in range(days + 1):
                d = today + dt.timedelta(days=i)
                grid[d] = {c: None for c in cols}
            for s in slots:
                d = dt.date.fromisoformat(s["date"])
                if d in grid:
                    grid[d][(s["slot"], s["sub_area"], s["start"], s["end"])] = s
            a["cols"] = cols
            a["grid"] = grid
            a["sources"] = sorted({(s["source_title"] or "", s["source_url"] or "", (s["fetched_at"] or "")[:16]) for s in slots})
            a["certainty"] = sorted({s["certainty"] for s in slots})
            areas.append(a)
        f["areas"] = areas
        return f
    finally:
        con.close()


@app.get("/facility/{fid}", response_class=HTMLResponse)
def facility_page(request: Request, fid: str):
    f = facility_detail(fid)
    if not f:
        return HTMLResponse("<h1>施設が見つかりません</h1>", status_code=404)
    return templates.TemplateResponse(request, "facility.html", {"root": root_prefix(request), "f": f, "today": dt.date.today(), "path": f"facility/{fid}"})


@app.get("/facilities", response_class=HTMLResponse)
def facilities_page(request: Request):
    con = dbm.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT f.*, (SELECT COUNT(*) FROM area a WHERE a.facility_id=f.id) n_area FROM facility f ORDER BY kind, sort_key").fetchall()]
    finally:
        con.close()
    return templates.TemplateResponse(request, "facilities.html", {"root": root_prefix(request), "rows": rows, "path": "facilities"})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"root": root_prefix(request), "fx": facets(), "path": "about"})


@app.get("/healthz")
def healthz():
    con = dbm.connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM slot").fetchone()[0]
        last = dbm.get_meta(con, "last_ingest", {}) or {}
    finally:
        con.close()
    return {"ok": True, "slots": n, "last_ingest": last.get("finished"), "version": __version__}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nAllow: /\nSitemap: {PUBLIC_BASE}sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap():
    con = dbm.connect()
    try:
        ids = [r[0] for r in con.execute("SELECT id FROM facility ORDER BY sort_key").fetchall()]
    finally:
        con.close()
    today = dt.date.today().isoformat()
    urls = [PUBLIC_BASE, PUBLIC_BASE + "about", PUBLIC_BASE + "facilities"] + [PUBLIC_BASE + f"facility/{i}" for i in ids]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"<url><loc>{u}</loc><lastmod>{today}</lastmod></url>\n" for u in urls) + "</urlset>\n"
    return Response(content=body, media_type="application/xml")


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms():
    fx = facets()
    return (
        f"# {SITE_NAME}（名古屋市 デモ版）\n\n"
        "> 名古屋市のスポーツセンターと中学校体育館（地域スポーツセンター）について、個人で利用できる日・時間帯を、施設を横断して検索できるサイト。"
        "各施設が公開している予定表（PDF/HTML）と名古屋市の一覧PDFから「日時・種目・○×」という事実だけを取り込み、各行に出典と取得日時を付けている。\n\n"
        f"- 施設数: スポーツセンター {fx['n_center']}・中学校体育館 {fx['n_school']}\n"
        f"- 最終取り込み: {fx['last_ingest']}\n"
        "- 検索: ?date=today|tomorrow|YYYY-MM-DD&t=am|pm|night&sport=バドミントン&ward=緑区&kind=center|school\n"
        f"- API: {PUBLIC_BASE}api/search（同じパラメータ・JSON）、{PUBLIC_BASE}api/facilities\n"
        f"- 施設ページ: {PUBLIC_BASE}facility/<id>（14日分の○×表と出典）\n"
        f"- サイトマップ: {PUBLIC_BASE}sitemap.xml\n\n"
        "## 出典\n- 各スポーツセンターの公式サイト（指定管理者: JPN・名古屋市教育スポーツ協会 ほか）の個人利用予定表\n"
        "- 名古屋市「地域スポーツセンター一覧」PDF（中学校体育施設のスポーツ開放）\n- 名古屋おしえてダイヤル FAQ 724・726・728（開館時間・登録・予約の仕組み）\n\n"
        "## 免責\n予定は変更されることがある。○は「個人利用できる時間帯」で、混雑・満員は分からない。来場前に施設へ確認すること。\n"
    )
