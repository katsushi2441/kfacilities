"""○×の予定表 PDF を「日付×時間帯×面」の行に変換する汎用パーサ。

対応レイアウト（設定 `layout`）:
  rows_dates : 行=日付（「1 火 ○ ○ × …」）、列=時間帯×面      … 緑・東・千種・中村
  cols_dates : 列=日付（「9/7 9/8 …」や「8 9 10 …」）、行=時間帯（面は見出し行か行内） … 枇杷島・北

文字の位置（pdftotext -layout の桁）で列を合わせるので、空欄や縦書きの「休館日」が
混ざっても列がずれない。記号の意味は `SYMBOLS` を見る。LLM は使わない。
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
from dataclasses import dataclass

OPEN = {"○", "〇", "●", "◯", "◎"}
CLOSED = {"×", "✕", "✗", "Ｘ", "X", "x"}
NA = {"－", "―", "‐", "—", "ー", "-", "–", "─"}
PARTIAL = {"△", "▲"}
HOLIDAY = {"休"}
SYMBOL_RE = re.compile("[" + "".join(re.escape(c) for c in (OPEN | CLOSED | NA | PARTIAL | HOLIDAY)) + "]")
WDAYS = "月火水木金土日"
Z2H = str.maketrans("０１２３４５６７８９：／", "0123456789:/")


def status_of(sym: str) -> str:
    if sym in OPEN:
        return "open"
    if sym in PARTIAL:
        return "partial"
    if sym in CLOSED:
        return "closed"
    if sym in HOLIDAY:
        return "holiday"
    return "na"


@dataclass
class Cell:
    date: dt.date
    slot: str
    sub_area: str
    status: str
    note: str = ""


def pdf_text(path: str) -> str:
    try:
        return subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True, text=True).stdout
    except Exception:  # poppler が無い環境は pdfplumber にフォールバック
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text(layout=True) or "") for p in pdf.pages)


def norm_label(s: str) -> str:
    return re.sub(r"[\s　]", "", s).translate(Z2H)


def detect_year_month(text: str, today: dt.date | None = None) -> tuple[int, int]:
    """見出しから年月を決める。令和/西暦/「9月」だけ、の順に試す。"""
    today = today or dt.date.today()
    # 発行日・作成日・「○月○日現在」の行は対象外（本文の年月と取り違えない）
    t = "\n".join(l for l in text.translate(Z2H).splitlines() if not re.search(r"発行|作成|現在|更新|時点", l))
    m = re.search(r"令和\s*(\d{1,2})\s*年\s*(?:度\s*)?(\d{1,2})\s*月", t)
    if m:
        return 2018 + int(m.group(1)), int(m.group(2))
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})\s*月\s*(?:度|分)?", t)
    month = int(m.group(1)) if m else today.month
    year = today.year
    # 年をまたぐ月（例: 今が12月で「1月」）は翌年
    if month < today.month - 6:
        year += 1
    elif month > today.month + 6:
        year -= 1
    return year, month


def _slot_pattern(slots: list[str]) -> re.Pattern:
    alts = []
    for s in slots:
        s2 = norm_label(s)
        # 「午後1」「午後１」「午 後 １」のような揺れを吸収
        alts.append(r"\s?".join(re.escape(ch) for ch in s2))
    return re.compile("(" + "|".join(alts) + ")")


def parse_rows_dates(text: str, year: int, month: int, slots: list[str], sub_areas: list[str]) -> list[Cell]:
    """行=日付。記号は「時間帯ごとに面が並ぶ」順（午前板・午前畳・午後1板…）とみなす。"""
    cells: list[Cell] = []
    ncol = len(slots) * max(1, len(sub_areas))
    row_re = re.compile(r"^\s*(\d{1,2})\s+([" + WDAYS + r"])(.*)$")
    for line in text.translate(Z2H).splitlines():
        m = row_re.match(line)
        if not m:
            continue
        day = int(m.group(1))
        try:
            date = dt.date(year, month, day)
        except ValueError:
            continue
        syms = SYMBOL_RE.findall(m.group(3))
        subs = sub_areas or [""]
        if not syms:  # 記号が無い行＝休館日（緑の月曜など）
            for s in slots:
                for sa in subs:
                    cells.append(Cell(date, s, sa, "holiday", "休館"))
            continue
        rest = m.group(3)
        if len(syms) < ncol and re.search(r"休\s*館", rest):
            # 「休館日」「夜間休館」: 足りない分は休館
            syms = syms + ["休"] * (ncol - len(syms))
        elif len(syms) < ncol and syms and syms[-1] in NA:
            # 末尾の「-」が複数面をまとめて表している
            syms = syms + [syms[-1]] * (ncol - len(syms))
        if len(syms) != ncol:
            # 列数が合わない行は「不明」で残す（誤って○にしない）
            for s in slots:
                for sa in subs:
                    cells.append(Cell(date, s, sa, "unknown", f"列数不一致({len(syms)}/{ncol})"))
            continue
        i = 0
        for s in slots:
            for sa in subs:
                cells.append(Cell(date, s, sa, status_of(syms[i])))
                i += 1
    return cells


def parse_cols_dates(text: str, year: int, month: int, slots: list[str], sub_areas: list[str], today: dt.date | None = None) -> list[Cell]:
    """列=日付。日付見出し行の桁位置に、各行の記号を最寄りで割り当てる。"""
    cells: list[Cell] = []
    lines = text.translate(Z2H).splitlines()
    slot_re = _slot_pattern(slots)
    sub_re = re.compile("(" + "|".join(re.escape(norm_label(a)) for a in sub_areas) + ")") if sub_areas else None
    date_cols: list[tuple[int, dt.date]] = []
    cur_sub = sub_areas[0] if sub_areas else ""
    cur_month = month
    for line in lines:
        # 日付見出し: 「9/7 9/8 …」または「日 8 9 10 …」
        md = [(m.start(), int(m.group(1)), int(m.group(2))) for m in re.finditer(r"(\d{1,2})/(\d{1,2})", line)]
        if len(md) >= 5:
            date_cols = []
            for pos, mo, d in md:
                y = year if mo >= month or month - mo < 6 else year + 1
                try:
                    date_cols.append((pos, dt.date(y, mo, d)))
                except ValueError:
                    pass
            continue
        stripped = line.strip()
        if stripped.startswith("日") and not SYMBOL_RE.search(line):
            nums = [(m.start(), int(m.group(0))) for m in re.finditer(r"\b\d{1,2}\b", line)]
            if len(nums) >= 5:
                date_cols = []
                prev = 0
                mo = cur_month
                for pos, d in nums:
                    if d < prev:  # 月をまたいだ
                        mo = mo % 12 + 1
                    prev = d
                    y = year if mo >= month else year + 1
                    try:
                        date_cols.append((pos, dt.date(y, mo, d)))
                    except ValueError:
                        pass
                continue
        if not date_cols:
            continue
        # 面の見出し行（「板面」だけの行）
        if sub_re and norm_label(stripped) in {norm_label(a) for a in sub_areas}:
            cur_sub = next(a for a in sub_areas if norm_label(a) == norm_label(stripped))
            continue
        # 時間帯行
        head = line[: min(len(line), 30)]
        ms = slot_re.search(norm_label(head)) if head.strip() else None
        sub = cur_sub
        if sub_re:
            msub = sub_re.search(norm_label(head))
            if msub:
                sub = next(a for a in sub_areas if norm_label(a) == msub.group(1))
        if not ms:
            # 北の「9：00～12：00 畳面 …」のように時刻＋面だけの行は直前の時間帯を引き継ぐ
            if not (sub_re and sub_re.search(norm_label(head)) and cells):
                continue
            slot = cells[-1].slot
        else:
            slot = next(s for s in slots if norm_label(s) == re.sub(r"\s", "", ms.group(1)))
        for m in SYMBOL_RE.finditer(line):
            pos = m.start()
            col = min(date_cols, key=lambda c: abs(c[0] - pos))
            cells.append(Cell(col[1], slot, sub, status_of(m.group(0))))
    # 「休」が縦書きで1行にしか出ない日は、その日の全時間帯を休館にする
    hol_dates = {c.date for c in cells if c.status == "holiday"}
    for c in cells:
        if c.date in hol_dates and c.status in {"na", "unknown"}:
            c.status = "holiday"
    seen = {(c.date, c.slot, c.sub_area) for c in cells}
    for d in hol_dates:
        for s in slots:
            for sa in (sub_areas or [""]):
                if (d, s, sa) not in seen:
                    cells.append(Cell(d, s, sa, "holiday", "休館"))
    return cells


def parse(path: str, layout: str, slots: list[str], sub_areas: list[str], today: dt.date | None = None) -> tuple[list[Cell], dict]:
    text = pdf_text(path)
    cells: list[Cell] = []
    months = []
    for page in [p for p in text.split("\f") if p.strip()]:
        year, month = detect_year_month(page, today)
        months.append(f"{year}-{month:02d}")
        if layout == "rows_dates":
            cells += parse_rows_dates(page, year, month, slots, sub_areas)
        elif layout == "cols_dates":
            cells += parse_cols_dates(page, year, month, slots, sub_areas, today)
        else:
            raise ValueError(f"unknown layout: {layout}")
    info = {"months": months, "chars": len(text), "cells": len(cells), "open": sum(1 for c in cells if c.status == "open")}
    return cells, info
