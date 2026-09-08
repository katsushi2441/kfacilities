"""名古屋市「地域スポーツセンター一覧」PDF（中学校体育施設のスポーツ開放）を
中学校ごとの曜日ルールに変換する。

表の列（令和8年度版・pdfplumber の extract_tables で 22 列）:
  0 区 / 1 センター名 / 2 管理事務室TEL(下4桁-4桁) / 3 住所 / 4 最寄停留所 / 5 週休日 /
  6 日曜日の開放（団体(AM)/団体(PM)） / 7..12 月〜土の体育館 / 13.. 武道場・テニス・運動場 …
体育館の月〜土のセルは「団 体」= 団体利用日、「週 休 日」= 休み、
それ以外の文字列（卓球、バドミントン…）= 個人利用日の優先種目。
開放時間は表の注記どおり 月〜土 18:00〜21:00（日曜は団体のみ）。

他の自治体の一覧 PDF を使うときは、列番号と語彙を `ColumnMap` で差し替える。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pdfplumber

WEEKDAYS = ["月", "火", "水", "木", "金", "土"]


@dataclass
class ColumnMap:
    ward: int = 0
    name: int = 1
    tel: int = 2
    address: int = 3
    station: int = 4
    closed: int = 5
    sunday: int = 6
    weekdays: tuple = (7, 8, 9, 10, 11, 12)
    group_words: tuple = ("団体", "団 体")
    closed_words: tuple = ("週休日", "週 休 日", "休")
    tel_prefix: str = "052-"
    city_prefix: str = "名古屋市"
    open_start: str = "18:00"
    open_end: str = "21:00"


@dataclass
class School:
    ward: str
    name: str
    tel: str
    address: str
    station: str
    closed_day: str
    sunday: str
    weekday_cells: dict = field(default_factory=dict)  # 曜日 → 生セル
    individual_days: dict = field(default_factory=dict)  # 曜日 → 種目リスト
    group_days: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _clean(s: str | None) -> str:
    return re.sub(r"[\s　]+", "", (s or "").replace("\n", ""))


# 表のセルは列幅で文字が切れる（「ソフトバレーボ」「バスケッ」）ので、正規名の前方一致で戻す
CANONICAL_SPORTS = [
    "バドミントン", "卓球", "バレーボール", "ソフトバレーボール", "バスケットボール", "レク・バレーボール", "レク・インディアカ",
    "インディアカ", "ソフトテニス", "フットサル", "ミニバスケットボール", "バウンドテニス", "ユニホック", "ドッジボール",
    "剣道", "柔道", "空手", "空手道", "合気道", "少林寺拳法", "なぎなた", "太極拳", "ダンス", "体操", "エアロビクス", "ヨガ",
    "軟式野球", "ソフトボール", "サッカー", "テニス", "軽スポーツ", "レクリエーション", "ニュースポーツ", "ラージボール卓球",
]


def canonical_sport(p: str) -> str:
    p = p.replace("レク・・", "レク・")
    if p in CANONICAL_SPORTS:
        return p
    for c in CANONICAL_SPORTS:
        if len(p) >= 3 and c.startswith(p):
            return c
    if p.startswith("レク") and not p.startswith("レク・"):
        return canonical_sport("レク・" + p[2:])
    return p


def _sports(cell: str) -> list[str]:
    c = re.sub(r"[\s　]", "", cell.replace("\n", ""))
    c = re.sub(r"[（(].*?[）)]", "", c)
    # 「・」は「レク・バレーボール」の一部なので区切りにしない
    parts = [p for p in re.split(r"[、,/／]", c) if p]
    out = []
    for p in parts:
        p = canonical_sport(p)
        if p and p not in out:
            out.append(p)
    return out


def parse(pdf_path: str, cmap: ColumnMap | None = None) -> list[School]:
    cmap = cmap or ColumnMap()
    schools: list[School] = []
    ward = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or len(row) <= max(cmap.weekdays):
                        continue
                    tel = _clean(row[cmap.tel])
                    if not re.fullmatch(r"\d{3}-\d{4}", tel):
                        continue  # 見出し行など
                    w = _clean(row[cmap.ward]).replace("/", "")
                    if w:
                        ward = w
                    name = _clean(row[cmap.name])
                    s = School(
                        ward=ward,
                        name=name,
                        tel=cmap.tel_prefix + tel,
                        address=cmap.city_prefix + ward + _clean(row[cmap.address]),
                        station=_clean(row[cmap.station]),
                        closed_day=_clean(row[cmap.closed]),
                        sunday=_clean(row[cmap.sunday]),
                    )
                    for wd, col in zip(WEEKDAYS, cmap.weekdays):
                        raw = (row[col] or "").replace("\n", "")
                        c = _clean(raw)
                        s.weekday_cells[wd] = c
                        if not c or c in ("̶", "-", "－", "―"):
                            continue
                        if any(g in c for g in cmap.group_words):
                            s.group_days.append(wd)
                        elif any(x in c for x in cmap.closed_words):
                            continue
                        else:
                            if "※" in c:
                                s.notes.append(f"{wd}: {c}")
                                c = re.sub(r"※\d*", "", c)
                            s.individual_days[wd] = _sports(c)
                    schools.append(s)
    return schools


def to_rules(s: School, cmap: ColumnMap | None = None) -> list[dict]:
    cmap = cmap or ColumnMap()
    rules = []
    for wd, sports in s.individual_days.items():
        rules.append({"days": [wd], "start": cmap.open_start, "end": cmap.open_end, "label": "夜間", "sports": sports, "note": "個人利用日（優先種目: " + "・".join(sports) + "）"})
    if s.closed_day:
        rules.append({"days": [s.closed_day], "status": "closed", "label": "週休日"})
    return rules
