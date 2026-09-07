"""曜日ルールを日付の行に展開する。

ルールの書き方（YAML）:
  rules:
    - days: [月, 火, 水, 木, 土]      # 曜日。祝日は「祝」
      start: "09:30"
      end: "21:30"
      label: 終日                    # 省略時は「開場」
    - days: [金]
      nth: [2, 4, 5]                 # 第2・4・5金曜だけ
      start: "09:30"
      end: "18:00"
    - days: [金]
      nth: [1, 3]
      status: closed                 # 第1・3金曜は個人利用不可
      except_holiday: true           # 祝日なら適用しない
      label: 自主事業教室のみ
  closed 系のルールは open 系より優先する。
"""
from __future__ import annotations

import datetime as dt

WD = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}

try:
    import jpholiday  # type: ignore

    def is_holiday(d: dt.date) -> bool:
        return bool(jpholiday.is_holiday(d))
except Exception:  # pragma: no cover

    def is_holiday(d: dt.date) -> bool:
        return False


def nth_weekday(d: dt.date) -> int:
    return (d.day - 1) // 7 + 1


def rule_matches(rule: dict, d: dt.date) -> bool:
    days = rule.get("days") or []
    hol = is_holiday(d)
    if rule.get("except_holiday") and hol:
        return False
    if "祝" in days and hol:
        return True
    if rule.get("holiday_only") and not hol:
        return False
    wd = [k for k, v in WD.items() if v == d.weekday()][0]
    if days and wd not in days:
        return False
    if rule.get("nth") and nth_weekday(d) not in rule["nth"]:
        return False
    if rule.get("from") and d < dt.date.fromisoformat(str(rule["from"])):
        return False
    if rule.get("to") and d > dt.date.fromisoformat(str(rule["to"])):
        return False
    return True


def expand(rules: list[dict], start: dt.date, days: int = 60) -> list[dict]:
    """[{date, slot, start, end, status, note, sports}] を返す。"""
    out = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        opens = [r for r in rules if r.get("status", "open") != "closed" and rule_matches(r, d)]
        closes = [r for r in rules if r.get("status") == "closed" and rule_matches(r, d)]
        if closes:
            r = closes[0]
            out.append({"date": d, "slot": r.get("label") or "休", "start": None, "end": None, "status": "closed", "note": r.get("label") or "", "sports": r.get("sports")})
            continue
        for r in opens:
            out.append({"date": d, "slot": r.get("label") or "開場", "start": r.get("start"), "end": r.get("end"), "status": "open", "note": r.get("note") or "", "sports": r.get("sports")})
    return out
