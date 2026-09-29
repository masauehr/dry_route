"""ID-15 デモサーバー。ローカル実行・公開デプロイ（Render等）の両方に対応。

降水ナウキャストは「今から60分先まで」しか意味を持たないデータのため、GitHub Pagesのような
静的常時公開はできない（都度サーバー側で最新データを取りに行く必要がある）。
また対象JSONにはCORSヘッダーが無く、ブラウザから直接 fetch すると失敗するため、
本サーバーが気象庁・OSRMへの取得を代行し、結果だけをブラウザに返す。

このサーバーは書き込みAPIも機密データ（.env等）も持たない読み取り専用の公開データ中継のため、
DNSリバインディング対策のHostチェックは行わない（他の自作サーバー(disaster_sns_watch等)は
個人データを扱うため実施しているが、本アプリには該当しない）。

使い方:
  ローカル: python server.py [port]  → http://127.0.0.1:8793/
  公開デプロイ: 環境変数 PORT を使う（Render等が自動設定）
出典: 気象庁ホームページ（高解像度降水ナウキャスト）・OpenStreetMap（ルーティング）
"""
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import fetch_nowcast as fn
import route as R

WEB_DIR = Path(__file__).parent / "webapp"
DEFAULT_PORT = 8793


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/frames":
            return self._api_frames()
        if url.path == "/api/route":
            return self._api_route(parse_qs(url.query))
        return self._static(url.path)

    def _api_frames(self):
        try:
            base, vts = fn.current_forecast()
            self._send_json({"base": base, "validtimes": vts})
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": str(e)}, 502)

    def _api_route(self, q):
        try:
            waypoints = []
            for p in q["points"]:
                lat_s, lon_s = p.split(",")
                waypoints.append((float(lat_s), float(lon_s)))
            if len(waypoints) < 2:
                raise ValueError("2点以上必要")
            mode = q.get("mode", ["walk"])[0]
            if mode not in ("walk", "bike"):
                raise ValueError("mode は walk/bike のみ")
        except (KeyError, ValueError) as e:
            return self._send_json({"error": f"points（lat,lon形式を2つ以上、繰り返し指定）が必要: {e}"}, 400)
        speed = R.WALK_MPS if mode == "walk" else R.BIKE_MPS
        try:
            result = R.recommend(waypoints, speed, mode)
            self._send_json(result)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": str(e)}, 502)

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            return self._send(404, b"Not Found", "text/plain; charset=utf-8")
        ctype = "text/html; charset=utf-8"
        if target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.command} {urlparse(self.path).path}\n")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", DEFAULT_PORT))
    host = "127.0.0.1" if len(sys.argv) > 1 or "PORT" not in os.environ else "0.0.0.0"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
