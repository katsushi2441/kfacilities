# AI-START-HERE — Kurage 施設検索（kfacilities）を AI エージェントに任せるための設計書

このファイルは、Claude Code / Codex などの AI エージェントに「設置」「地域の追加」「更新の運用」を頼むときに、最初に読ませる設計書です。人が読んでも分かるように書いてあります。

## 1. これは何か

地域が公開している **個人利用できる日時の予定表（PDF・HTML）** を取り込み、**日付・時間帯・種目・区・現在地** で横断検索する Web アプリと、その取り込みキットです。

- 検索は SQLite の SQL だけ。**利用のたびに LLM を呼ばない**。
- 取り込むのは「日時・種目・○×」という事実だけ。各行に **出典 URL と取得日時** を持つ。予定表そのものは転載しない。
- 名古屋市版（スポーツセンター15＋中学校体育館111校）が `regions/nagoya.yaml` に入っている。

## 2. ディレクトリ

```
kfacilities/            アプリ本体（FastAPI）
  app.py                検索UI・施設ページ・/api/search・sitemap・llms.txt
  ingest.py             取り込み CLI（python -m kfacilities.ingest regions/<地域>.yaml）
  fetch.py              取得（1秒間隔・キャッシュ outputs/raw/）とリンク発見（discover）
  geocode.py            住所→緯度経度（国土地理院API・DBにキャッシュ）
  db.py                 SQLite スキーマ（facility / area / slot / geocode / meta）
  parsers/pdf_grid.py   ○×予定表PDF（rows_dates / cols_dates）
  parsers/weekly_rules.py 曜日ルール→日付展開（祝日は jpholiday）
  parsers/chiiki_table.py 一覧PDFの表→学校ごとの曜日ルール（ColumnMap で列を差し替え）
  templates/ static/    画面（ライトテーマ固定・スマホ幅で横スクロールしない）
regions/nagoya.yaml     施設・場所・出典・レイアウト・曜日ルール（この形で他地域を書く）
tests/                  実PDF7本の回帰テスト（pytest）
scripts/kfacilities.service  systemd user unit の雛形
scripts/deploy_proxy.sh php/kfacilities.php  公開用の透過プロキシ（PHP が動くレンタルサーバーに置く）
outputs/                取得物・SQLite・取り込みログ（git 管理外）
```

## 3. 設置（AI に頼む指示文 1）

> この repo を `uv venv .venv` と `uv pip install --python .venv/bin/python fastapi uvicorn jinja2 pdfplumber pyyaml requests beautifulsoup4 lxml jpholiday` でセットアップし、`regions/nagoya.yaml` を取り込んで（`python -m kfacilities.ingest regions/nagoya.yaml`）、`uvicorn kfacilities.app:app --port <空きポート>` で起動して、`/healthz` が `ok: true` かつ `slots > 0` になることを確認してください。ポートは `ss -ltn` で空きを実測して決めてください。poppler-utils（pdftotext）があれば入れてください（無くても pdfplumber で動きます）。

確認ポイント: `/?date=today&t=night&sport=バドミントン` で結果が出る／`/facility/midori` に○×表が出る／`/sitemap.xml` と `/llms.txt` が 200。

## 4. 地域を追加する（AI に頼む指示文 2）

> `regions/nagoya.yaml` を手本に `regions/<地域>.yaml` を作ってください。各施設について、公式サイトで「個人利用」の予定がどう公開されているかを調べ、①○×のPDFなら `source: {type: pdf_grid, ...}`、②文章だけなら `rules:`、③自治体の一覧PDFなら `school_lists:` で書いてください。PDF は `outputs/raw/` に落ちるので、`pdftotext -layout` で見て `layout`（行=日付なら rows_dates、列=日付なら cols_dates）と `sub_areas`（板面/畳面 など面の並び）を決めてください。取り込み後、`outputs/ingest-<地域>-latest.json` で `unknown` が多い area があれば、その PDF の行を見てパーサの前提（記号・列数）と合っているか確認してください。転載条件（自治体サイトは二次利用の表示が無ければ許諾が必要）を守り、事実の抽出と出典リンクに留めてください。

`pdf_grid` の書き方:

```yaml
source:
  type: pdf_grid
  page: https://example.jp/facility/        # 起点ページ
  discover:                                  # 上から順に辿る
    - {text: '個人利用可能日', take: 1}      # リンク文字で「お知らせ」を1件
    - {href: '\.pdf$'}                       # その中の PDF
  layout: rows_dates                         # または cols_dates
  sub_areas: [板面, 畳面]                    # 面が無ければ省略
  confirm_days: 14                           # 何日先までを「確定」とするか
```

`rules` の書き方（第1・3金曜休、祝日の扱い、期間指定）:

```yaml
rules:
  - {days: [月, 火, 水, 木, 土], start: "09:30", end: "21:30", label: 開場, except_holiday: true}
  - {days: [日, 祝], start: "09:30", end: "18:00", label: 開場}
  - {days: [金], nth: [1, 3], status: closed, label: 自主事業教室のみ, except_holiday: true}
  - {from: "2026-05-01", to: "2027-01-04", status: closed, label: 利用休止}
```

## 5. 更新の運用（AI に頼む指示文 3）

> 週1回、`python -m kfacilities.ingest regions/<地域>.yaml` を実行し、`outputs/ingest-<地域>-latest.json` の `error` と `rows: 0` の area を報告してください。予定表の URL が変わって `pdfs: []` になった施設は、公式サイトで新しいリンク先を探して `discover` を直してください。取り込み後は `/healthz` の `last_ingest` が更新されていることを確認してください。

多くの施設は「利用日の14日前に確定」なので、週1回で足ります。取り込みは施設サイトに 1 秒間隔でしかアクセスしません。

## 6. 変えてはいけないこと

- 検索経路に LLM を入れない（費用と再現性のため）。新しい PDF レイアウトの初回解釈に AI を使うのは可。
- 予定表・表の画像や本文を転載しない。事実（日時・種目・○×）と出典リンク・取得日時だけ。
- ライトテーマ固定。スマホ幅（320px）で横スクロールが出ないこと。
- 列数が合わない行を勝手に「○」にしない（`unknown` のまま残す）。
