# CLAUDE.md — dry_route

## このプロジェクトの位置づけ
自転車/徒歩ナビの「濡れない経路」。気象庁の高解像度降水ナウキャストで経路上の降雨を判定し、
出発時刻を提案する。[weather_hackathon_ideas](https://github.com/masauehr/weather_hackathon_ideas)
のアイデア ID-15 として検証しGOだったため独立。

## 🔑 データ利用の絶対ルール
- **気象データは気象庁 bosai から取得する。** Open-Meteo / ERA5 など再解析・第三者APIは使わない。GRIB（数値予報GPV）は扱わない。
- **個人データ・非公開データは使わない。**
- 道路ルーティングは OSRM（`routing.openstreetmap.de`、OpenStreetMapデータ）。**デモ用途の公開サーバー**であり、商用・大量アクセスは利用規約違反になるため行わない。

## 作業ルール
- ドキュメントは日本語。Markdown。コードコメントも日本語。
- Python は `requests` / `Pillow` のみ依存（軽量構成）。Web UI は Vanilla JS / HTML / CSS + Leaflet（CDN読み込み）。
- `server.py` は書き込みAPI・機密データを持たない読み取り専用の公開データ中継。APIキーは使わない（`.env` も無い）。
- 気象庁・OSRM・OpenStreetMapの利用規約を遵守。図表・UIには出典を明記。

## 実行・デプロイ
- ローカル: `python server.py [port]`（既定8793）
- 公開デプロイ: `PORT` 環境変数を読む（Render等が自動設定）。デプロイ手順は README.md 参照。
- ナウキャストは「今から60分先まで」しか意味を持たないため、GitHub Pages等の静的公開は不可（README.md「なぜサーバーが必要か」参照）。

## GitHub更新ルール
- ファイルを変更・追加した場合は、必ず `git add` → `git commit` → `git push` まで行う。
- push前にユーザーの確認を求める（破壊的操作の場合は特に）。
