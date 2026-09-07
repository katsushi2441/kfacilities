"""取り込み CLI。

  .venv/bin/python -m kfacilities.ingest regions/nagoya.yaml [--days 60] [--no-geocode]

設定ファイル（regions/*.yaml）の facilities を DB に upsert し、各 area の
source（pdf_grid / rules / chiiki_table）から slot 行を作る。
取り込みのたびに、その area・その source の行は全部入れ替える（古い予定を残さない）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import traceback

import yaml

from . import db as dbm
from . import fetch, geocode
from .parsers import chiiki_table, pdf_grid, weekly_rules

DEFAULT_SLOTS = {"午前": ["09:00", "12:00"], "午後1": ["12:00", "15:00"], "午後2": ["15:00", "18:00"], "夜間": ["18:00", "21:00"]}


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def upsert_facility(con, region: str, f: dict, sort_key: int = 0) -> None:
    con.execute(
        """INSERT INTO facility(id,region,name,kind,ward,address,postal,tel,url,hours,closed,fee,note,operator,station,sort_key)
           VALUES(:id,:region,:name,:kind,:ward,:address,:postal,:tel,:url,:hours,:closed,:fee,:note,:operator,:station,:sort_key)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,ward=excluded.ward,address=excluded.address,
             postal=excluded.postal,tel=excluded.tel,url=excluded.url,hours=excluded.hours,closed=excluded.closed,fee=excluded.fee,
             note=excluded.note,operator=excluded.operator,station=excluded.station,sort_key=excluded.sort_key""",
        {
            "id": f["id"], "region": region, "name": f["name"], "kind": f.get("kind", "center"), "ward": f.get("ward"),
            "address": f.get("address"), "postal": f.get("postal"), "tel": f.get("tel"), "url": f.get("url"), "hours": f.get("hours"),
            "closed": f.get("closed"), "fee": f.get("fee"), "note": f.get("note"), "operator": f.get("operator"), "station": f.get("station"),
            "sort_key": sort_key,
        },
    )


def upsert_area(con, facility_id: str, a: dict) -> str:
    aid = f"{facility_id}:{a['name']}"
    con.execute(
        """INSERT INTO area(id,facility_id,name,sports,fee,note,source_page) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET sports=excluded.sports,fee=excluded.fee,note=excluded.note,source_page=excluded.source_page""",
        (aid, facility_id, a["name"], json.dumps(a.get("sports") or [], ensure_ascii=False), a.get("fee"), a.get("note"), (a.get("source") or {}).get("page") or a.get("source_page")),
    )
    return aid


def replace_slots(con, area_id: str, rows: list[dict]) -> None:
    con.execute("DELETE FROM slot WHERE area_id=?", (area_id,))
    con.executemany(
        """INSERT OR REPLACE INTO slot(area_id,date,slot,sub_area,start,end,status,certainty,sports,source_url,source_title,fetched_at,note)
           VALUES(:area_id,:date,:slot,:sub_area,:start,:end,:status,:certainty,:sports,:source_url,:source_title,:fetched_at,:note)""",
        rows,
    )


def discover(source: dict) -> list[dict]:
    """source.discover の手順を上から実行して PDF の URL 一覧を返す。

    discover:
      - page: https://…/            # 起点ページ
        text: 個人利用可能日          # リンク文字の正規表現（省略可）
        href: \\.pdf$                # href の正規表現（省略可）
        take: 1                      # 上から何件（省略=全部）
      - href: \\.pdf$                # 前段で見つけたページの中をさらに探す
    """
    urls = [{"url": source["page"], "title": ""}] if source.get("page") else []
    steps = source.get("discover") or [{"href": source.get("pdf_regex", r"\.pdf(\?|$)")}]
    for step in steps:
        nxt = []
        pages = [{"url": step["page"], "title": ""}] if step.get("page") else urls
        for pg in pages:
            found = fetch.find_links(pg["url"], step.get("href", r"."), text_pattern=step.get("text"))
            if step.get("take"):
                found = found[: int(step["take"])]
            nxt.extend(found)
        urls = nxt
    # 同じ URL は 1 回
    seen, out = set(), []
    for u in urls:
        if u["url"] not in seen:
            seen.add(u["url"])
            out.append(u)
    return out


def ingest_pdf_grid(con, area_id: str, a: dict, slots_def: dict, today: dt.date) -> dict:
    src = a["source"]
    slots = src.get("slots") or list(slots_def.keys())
    subs = src.get("sub_areas") or []
    confirm_days = int(src.get("confirm_days", 14))
    pdfs = discover(src)
    if src.get("latest"):
        pdfs = pdfs[-int(src["latest"]):] if src.get("order") == "asc" else pdfs[: int(src["latest"])]
    rows, info_all = [], []
    for pdf in pdfs:
        try:
            content, path = fetch.get(pdf["url"], binary=True)
        except Exception as e:
            info_all.append({"url": pdf["url"], "error": str(e)})
            continue
        cells, info = pdf_grid.parse(path, src["layout"], slots, subs, today)
        info["url"] = pdf["url"]
        info_all.append(info)
        for c in cells:
            if c.date < today - dt.timedelta(days=1):
                continue
            st = c.status
            certainty = "確定" if (c.date - today).days <= confirm_days else "予定"
            if src.get("certainty"):
                certainty = src["certainty"]
            rows.append({
                "area_id": area_id, "date": c.date.isoformat(), "slot": c.slot, "sub_area": c.sub_area,
                "start": (slots_def.get(c.slot) or [None, None])[0], "end": (slots_def.get(c.slot) or [None, None])[1],
                "status": st, "certainty": certainty, "sports": json.dumps(a.get("sports") or [], ensure_ascii=False),
                "source_url": pdf["url"], "source_title": pdf.get("title") or os.path.basename(pdf["url"]), "fetched_at": now_iso(), "note": c.note,
            })
    replace_slots(con, area_id, rows)
    return {"rows": len(rows), "pdfs": info_all}


def ingest_rules(con, area_id: str, a: dict, today: dt.date, days: int, source_url: str | None, title: str | None) -> dict:
    exp = weekly_rules.expand(a["rules"], today, days)
    rows = []
    for r in exp:
        rows.append({
            "area_id": area_id, "date": r["date"].isoformat(), "slot": r["slot"], "sub_area": "", "start": r["start"], "end": r["end"],
            "status": r["status"], "certainty": a.get("certainty", "規則"), "sports": json.dumps(r.get("sports") or a.get("sports") or [], ensure_ascii=False),
            "source_url": source_url or a.get("source_page"), "source_title": title or a.get("source_title") or "公式サイトの利用案内", "fetched_at": now_iso(), "note": r.get("note") or "",
        })
    replace_slots(con, area_id, rows)
    return {"rows": len(rows)}


def ingest_chiiki(con, region: str, spec: dict, today: dt.date, days: int, sort_base: int) -> dict:
    content, path = fetch.get(spec["pdf"], binary=True)
    schools = chiiki_table.parse(path)
    n = 0
    for i, s in enumerate(schools):
        fid = "school-" + re.sub(r"[^0-9a-zA-Z一-龥ぁ-んァ-ン]", "", s.name)
        f = {
            "id": fid, "name": f"{s.name}地域スポーツセンター（{s.name}中学校）", "kind": "school", "ward": s.ward, "address": s.address,
            "tel": s.tel, "url": spec.get("page"), "hours": spec.get("hours", "月〜土 18:00〜21:00（個人利用は平日夜間）"),
            "closed": f"{s.closed_day}曜日" if s.closed_day else None, "fee": spec.get("fee", "無料（個人利用登録が必要）"),
            "note": "；".join(s.notes) or None, "operator": spec.get("operator", "名古屋市（地域スポーツセンター）"), "station": s.station,
        }
        upsert_facility(con, region, f, sort_base + i)
        a = {"name": "体育館", "sports": sorted({x for v in s.individual_days.values() for x in v}), "source_page": spec.get("page"), "rules": chiiki_table.to_rules(s), "certainty": "年度予定"}
        aid = upsert_area(con, fid, a)
        ingest_rules(con, aid, a, today, days, spec["pdf"], spec.get("title", "地域スポーツセンター一覧（PDF）"))
        n += 1
    return {"schools": n}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("region_yaml")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD（テスト用）")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(open(args.region_yaml, encoding="utf-8"))
    region = cfg["region"]
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    slots_def = cfg.get("slots") or DEFAULT_SLOTS
    report = {"region": region, "started": now_iso(), "facilities": {}}
    with dbm.tx() as con:
        for i, f in enumerate(cfg.get("facilities", [])):
            upsert_facility(con, region, f, i)
            rep = {}
            for a in f.get("areas", []):
                aid = upsert_area(con, f["id"], a)
                try:
                    if a.get("source", {}).get("type") == "pdf_grid":
                        rep[a["name"]] = ingest_pdf_grid(con, aid, a, slots_def, today)
                    elif a.get("rules"):
                        rep[a["name"]] = ingest_rules(con, aid, a, today, args.days, a.get("source_page") or f.get("url"), a.get("source_title"))
                except Exception as e:  # 1 施設の失敗で全体を止めない
                    rep[a["name"]] = {"error": f"{type(e).__name__}: {e}"}
                    traceback.print_exc()
            report["facilities"][f["id"]] = rep
            print(f"[{f['id']}] {json.dumps(rep, ensure_ascii=False)[:300]}")
        for j, spec in enumerate(cfg.get("school_lists", [])):
            try:
                r = ingest_chiiki(con, region, spec, today, args.days, 1000 + j * 1000)
            except Exception as e:
                r = {"error": f"{type(e).__name__}: {e}"}
                traceback.print_exc()
            report.setdefault("school_lists", []).append(r)
            print(f"[school_list] {r}")
        if not args.no_geocode:
            for row in con.execute("SELECT id,address FROM facility WHERE (lat IS NULL OR lng IS NULL) AND address IS NOT NULL").fetchall():
                ll = geocode.lookup(con, row["address"])
                if ll:
                    con.execute("UPDATE facility SET lat=?,lng=? WHERE id=?", (ll[0], ll[1], row["id"]))
        report["finished"] = now_iso()
        dbm.set_meta(con, "last_ingest", report)
        dbm.set_meta(con, "region_name", cfg.get("name", region))
    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs"), exist_ok=True)
    json.dump(report, open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", f"ingest-{region}-latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("DONE", report["finished"])


if __name__ == "__main__":
    sys.exit(main())
