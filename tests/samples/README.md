# tests/samples

回帰テストで使う実物の予定表 PDF（緑・東・千種・中村・枇杷島・北スポーツセンターの第2競技場個人利用、名古屋市の地域スポーツセンター一覧）。
配布パッケージには**同梱しない**（各施設・名古屋市の公開物のため）。`python -m kfacilities.ingest regions/nagoya.yaml` を一度回すと
`outputs/raw/` に同じ PDF が落ちるので、ここへコピーすると `pytest tests/` が通る（ファイル名は tests/test_parsers.py 参照）。
