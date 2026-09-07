"""実 PDF（tests/samples）で ○× パーサと一覧表パーサの解釈を固定する。"""
import datetime as dt
import os

from kfacilities.parsers import chiiki_table, pdf_grid, weekly_rules

S = os.path.join(os.path.dirname(__file__), "samples")
SLOTS = ["午前", "午後1", "午後2", "夜間"]
TODAY = dt.date(2026, 9, 8)


def cell(cells, d, slot, sub):
    m = [c for c in cells if c.date == d and c.slot == slot and c.sub_area == sub]
    return m[0].status if m else None


def test_rows_dates_midori():
    cells, info = pdf_grid.parse(f"{S}/dai2_9_midori.pdf", "rows_dates", SLOTS, ["板面", "畳面"], TODAY)
    assert info["months"] == ["2026-09"]  # 発行日(8月)に引きずられない
    assert cell(cells, dt.date(2026, 9, 1), "午後1", "板面") == "closed"
    assert cell(cells, dt.date(2026, 9, 1), "午後1", "畳面") == "open"
    assert cell(cells, dt.date(2026, 9, 7), "午前", "板面") == "holiday"  # 月曜: 記号なし
    assert cell(cells, dt.date(2026, 9, 6), "夜間", "板面") == "na"


def test_rows_dates_short_rows_chikusa():
    cells, _ = pdf_grid.parse(f"{S}/jpn_chikusa.pdf", "rows_dates", SLOTS, ["板面", "タタミ面"], TODAY)
    assert cell(cells, dt.date(2026, 9, 6), "夜間", "板面") == "holiday"  # 「夜間休館」
    assert cell(cells, dt.date(2026, 9, 4), "午前", "板面") == "holiday"  # 「休館日」
    assert cell(cells, dt.date(2026, 9, 22), "午前", "板面") == "open"


def test_rows_dates_two_pages_nakamura():
    cells, info = pdf_grid.parse(f"{S}/nespa_nakamura.pdf", "rows_dates", SLOTS, ["板面", "畳面"], TODAY)
    assert info["months"] == ["2026-09", "2026-10"]
    assert cell(cells, dt.date(2026, 9, 6), "夜間", "畳面") == "na"  # 末尾の「-」が両面
    assert cell(cells, dt.date(2026, 10, 4), "午前", "板面") == "closed"


def test_cols_dates_biwajima_and_kita():
    cells, _ = pdf_grid.parse(f"{S}/nespa_biwajima.pdf", "cols_dates", SLOTS, ["板面", "畳面"], TODAY)
    assert cell(cells, dt.date(2026, 9, 11), "午前", "板面") == "open"
    assert cell(cells, dt.date(2026, 9, 18), "午前", "板面") == "holiday"
    assert cell(cells, dt.date(2026, 9, 10), "午前", "畳面") == "open"
    cells, _ = pdf_grid.parse(f"{S}/nespa_kita.pdf", "cols_dates", SLOTS, ["板面", "畳面"], TODAY)
    assert cell(cells, dt.date(2026, 9, 8), "午後2", "板面") == "open"
    assert cell(cells, dt.date(2026, 9, 18), "午前", "板面") == "holiday"  # 縦書きの「休館日」


def test_all_closed_higashi():
    cells, _ = pdf_grid.parse(f"{S}/jpn_higashi.pdf", "rows_dates", SLOTS, ["板面", "畳面"], TODAY)
    assert cells and all(c.status == "holiday" for c in cells)


def test_chiiki_table():
    schools = chiiki_table.parse(f"{S}/ichiran2026.pdf")
    assert len(schools) >= 100
    s = next(x for x in schools if x.name == "振甫")
    assert s.ward == "千種区" and s.tel.startswith("052-") and s.closed_day
    assert any(v for v in s.individual_days.values())


def test_weekly_rules_nth_and_holiday():
    rules = [
        {"days": ["月", "火", "水", "木", "土"], "start": "09:30", "end": "21:30"},
        {"days": ["金"], "nth": [1, 3], "status": "closed", "except_holiday": True},
        {"days": ["金"], "nth": [2, 4, 5], "start": "09:30", "end": "18:00"},
    ]
    rows = weekly_rules.expand(rules, dt.date(2026, 9, 1), 30)
    by = {r["date"]: r for r in rows}
    assert by[dt.date(2026, 9, 4)]["status"] == "closed"  # 第1金曜
    assert by[dt.date(2026, 9, 11)]["status"] == "open" and by[dt.date(2026, 9, 11)]["end"] == "18:00"  # 第2金曜
