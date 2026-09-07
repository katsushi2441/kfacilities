#!/bin/bash
# 公開入口 php/kfacilities.php を heteml (kurage.exbridge.jp) へ FTP 配置する。
# バックエンド設定 kfacilities_config.php は初回だけ生成して同じ場所に置く（リポジトリには含めない）。
# 認証情報は aixec/.env の FTP_HOST / FTP_USER / FTP_PASS。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . /home/kojima/work/aixec/.env; set +a
BACKEND="${KFACILITIES_BACKEND_URL:-http://exbridge.ddns.net:18384}"
TMP=$(mktemp)
printf '<?php define("KFACILITIES_BACKEND", "%s");\n' "$BACKEND" > "$TMP"
curl -sS -T php/kfacilities.php "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}/web/kurage_exbridge_jp/kfacilities.php"
curl -sS -T "$TMP" "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}/web/kurage_exbridge_jp/kfacilities_config.php"
rm -f "$TMP"
echo "deployed: https://kurage.exbridge.jp/kfacilities.php/"
curl -s -o /dev/null -w "public: %{http_code}\n" "https://kurage.exbridge.jp/kfacilities.php/"
