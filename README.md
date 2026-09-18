# Kurage 施設検索（kfacilities）

地域が公開している **PDF・HTML の「個人利用できる日時」を取り込み、条件（日付・時間帯・種目・区・現在地）で横断検索する** オンプレミスキットです。
名古屋市版（スポーツセンター15施設＋中学校体育館の個人開放111校）をデモとして同梱しています。

- デモ: https://kurage.exbridge.jp/kfacilities.php/
- 検索は SQLite の SQL だけで、**LLM は使いません**（利用のたびに費用が発生しない）。
- 取り込むのは「日時・種目・○×」という事実だけ。各行に出典 URL と取得日時を付け、予定表そのものは転載しません。

## 仕組み

```
regions/nagoya.yaml  ── 施設・場所・出典・レイアウト・曜日ルール
        │
        ▼  python -m kfacilities.ingest regions/nagoya.yaml
kfacilities/fetch.py        取得（キャッシュ・1秒間隔・UA明示）＋ページ内リンク発見（discover 手順）
kfacilities/parsers/
   pdf_grid.py              ○×の予定表 PDF（行=日付 / 列=日付 の両レイアウト。文字の桁位置で列合わせ）
   weekly_rules.py          曜日ルール（第1・3金曜休、祝日、期間指定）→ 日付に展開（jpholiday）
   chiiki_table.py          一覧 PDF の表（pdfplumber）→ 学校ごとの曜日ルール
kfacilities/geocode.py      住所→緯度経度（国土地理院 API・キャッシュ）
        │
        ▼
outputs/kfacilities.sqlite  facility / area / slot（施設×場所×日付×時間帯×面）
        │
        ▼  uvicorn kfacilities.app:app --port 18384
kfacilities/app.py          検索UI・施設ページ（14日表）・/api/search・sitemap・llms.txt・JSON-LD
```

## セットアップ

```bash
uv venv .venv && uv pip install --python .venv/bin/python fastapi uvicorn jinja2 pdfplumber pyyaml requests beautifulsoup4 lxml jpholiday
# poppler-utils（pdftotext）があると速い。無ければ pdfplumber にフォールバック
.venv/bin/python -m kfacilities.ingest regions/nagoya.yaml        # 取り込み（初回は地理院ジオコーディングで数分）
.venv/bin/uvicorn kfacilities.app:app --host 127.0.0.1 --port 18384
```

常駐は `scripts/kfacilities.service`（systemd user unit・Restart=always）。公開は `php/kfacilities.php`（透過プロキシ）を `scripts/deploy_proxy.sh` で配置。

更新は週1回の再取り込み（多くの施設は「利用日の14日前に確定」）。定期実行は運用側のジョブ基盤に載せる。

## 別の地域に差し替える

`regions/<地域>.yaml` を書くだけです。

| 出典の形 | 書き方 |
|---|---|
| ○×の予定表 PDF | `source: {type: pdf_grid, page: 起点URL, discover: [{text: 個人利用可能日, take: 1}, {href: '\.pdf$'}], layout: rows_dates または cols_dates, sub_areas: [板面, 畳面], confirm_days: 14}` |
| HTML の文章だけ（曜日ルール） | `rules: [{days: [月,火], start: "09:30", end: "21:30"}, {days: [金], nth: [1,3], status: closed}]` |
| 自治体の一覧 PDF（表） | `school_lists: [{pdf: URL, page: URL}]`（列の対応は `parsers/chiiki_table.py` の `ColumnMap`） |

- `discover` は上から順に「ページ → リンク → ページ → PDF」と辿る手順。`text` はリンク文字、`href` は URL の正規表現。
- `pdf_grid` の記号: ○●◯◎＝個人利用可、×✕＝専用利用、休＝休館、－―＝該当なし、△＝一部。列数が合わない行は「不明」で残し、誤って○にしません。
- 年月は見出し（「令和8年度 9月」「9月度」）から判定し、「発行日」「○月○日現在」の行は無視します。複数ページの PDF はページごとに判定します。

## テスト

```bash
.venv/bin/python -m pytest -q tests/     # tests/samples/ の実 PDF 7本で解釈を確認
```

## 出典と免責（名古屋市版）

- 各スポーツセンター公式サイト（指定管理者: JPN・名古屋市教育スポーツ協会 ほか）の個人利用予定表・利用案内
- 名古屋市「地域スポーツセンター一覧」PDF（中学校体育施設のスポーツ開放）、名古屋おしえてダイヤル FAQ 724・726・728
- 国土地理院 地名検索 API

予定は変更されることがあります。○は「個人利用できる時間帯」で、混雑・満員は分かりません。名古屋市公式サイトの内容は二次利用の表示が無いものは担当課の許諾が必要とされているため、事実の抽出とリンクに留めています。

## ライセンス

MIT（ソースコード）。取り込んだデータの権利は各出典元にあります。
