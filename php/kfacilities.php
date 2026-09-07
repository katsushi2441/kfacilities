<?php
// Kurage 施設検索 (kfacilities) — kurage.exbridge.jp 上の公開入口。
// 自宅サーバー :18384 への透過プロキシ。UI は相対パスなので
// /kfacilities.php/ (末尾スラッシュ) を起点に PATH_INFO で中継する。
// バックエンド URL は同ディレクトリの kfacilities_config.php で定義する（リポジトリには含めない）
//   <?php define('KFACILITIES_BACKEND', 'http://あなたのサーバー:18384');
$__cfg = __DIR__ . '/kfacilities_config.php';
if (is_file($__cfg)) { require_once $__cfg; }
$BACKEND = defined('KFACILITIES_BACKEND') ? KFACILITIES_BACKEND : 'http://127.0.0.1:18384';

if (!isset($_SERVER['PATH_INFO']) || $_SERVER['PATH_INFO'] === '') {
    if (substr($_SERVER['REQUEST_URI'], -1) !== '/' && strpos($_SERVER['REQUEST_URI'], '?') === false) {
        header('Location: /kfacilities.php/', true, 302); exit;
    }
}
$path = isset($_SERVER['PATH_INFO']) ? $_SERVER['PATH_INFO'] : '/';
$qs = isset($_SERVER['QUERY_STRING']) && $_SERVER['QUERY_STRING'] !== '' ? '?' . $_SERVER['QUERY_STRING'] : '';

$ch = curl_init($BACKEND . $path . $qs);
$headers = array('X-Forwarded-Proto: https', 'X-Forwarded-Host: kurage.exbridge.jp');
if (!empty($_SERVER['HTTP_X_FORWARDED_FOR'])) { $headers[] = 'X-Forwarded-For: ' . $_SERVER['HTTP_X_FORWARDED_FOR']; }
elseif (!empty($_SERVER['REMOTE_ADDR'])) { $headers[] = 'X-Forwarded-For: ' . $_SERVER['REMOTE_ADDR']; }
if (!empty($_SERVER['HTTP_USER_AGENT'])) { $headers[] = 'User-Agent: ' . $_SERVER['HTTP_USER_AGENT']; }
curl_setopt_array($ch, array(
    CURLOPT_CUSTOMREQUEST => $_SERVER['REQUEST_METHOD'],
    CURLOPT_RETURNTRANSFER => true, CURLOPT_HEADER => true,
    CURLOPT_HTTPHEADER => $headers, CURLOPT_ENCODING => '',
    CURLOPT_TIMEOUT => 60, CURLOPT_FOLLOWLOCATION => false,
));
if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
}
$res = curl_exec($ch);
if ($res === false) { http_response_code(502); header('Content-Type: text/plain; charset=utf-8');
    echo 'Kurage 施設検索のバックエンドに接続できません'; exit; }
$status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
$hsize = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
$ctype = (string)curl_getinfo($ch, CURLINFO_CONTENT_TYPE);
curl_close($ch);
http_response_code($status);
foreach (explode("\r\n", substr($res, 0, $hsize)) as $h) {
    if (stripos($h, 'Content-Type:') === 0 || stripos($h, 'Cache-Control:') === 0) { header($h); }
}
$body = substr($res, $hsize);
// 計測タグ（kurage 系は kurage.exbridge.jp の simpletrack）を HTML にだけ差し込む
if (stripos($ctype, 'text/html') !== false) {
    $tag = '<script>(function(){var s=document.createElement("script");s.src="https://kurage.exbridge.jp/simpletrack.php?url="+encodeURIComponent(location.href)+"&ref="+encodeURIComponent(document.referrer);document.head.appendChild(s)})();</script>';
    $body = str_replace('</head>', $tag . '</head>', $body);
}
echo $body;
