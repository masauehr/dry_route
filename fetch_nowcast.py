"""ID-15 検証: 気象庁 高解像度降水ナウキャスト（hrpns）を取得し、緯度経度1点の降雨有無を調べる。

タイルは 256x256・4bitパレット(最大16色)のPNGで、パレットindexごとに tRNS で透明度が
入っている（実測: index0/1 が alpha=0=降水なし、index2以降が可視の降水強度色）。
そのため「αチャンネル > 0 なら、その地点・その時刻は雨が降っている」と機械的に判定できる。
GRIB（数値予報GPV）は使わず、公開のタイルPNGのみを使う。

出典: 気象庁ホームページ（高解像度降水ナウキャスト）
"""
import io
import math
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

BASE = "https://www.jma.go.jp/bosai"
UA = {"User-Agent": "weather-hackathon-spike (personal research)"}
ZOOM = 10  # hrpns の実質的な最大解像度に近いズーム（jma系実装の nativeMax=10 を参考）
TILE_PX = 256

_tile_cache = {}


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r


def target_times():
    """直近の実況(N1)と、60分先までの予測(N2)の basetime/validtime 一覧を返す。"""
    obs = get(f"{BASE}/jmatile/data/nowc/targetTimes_N1.json").json()
    fcst = get(f"{BASE}/jmatile/data/nowc/targetTimes_N2.json").json()
    return obs, fcst


def current_forecast():
    """予測(N2)リストの基準時刻と、60分先までの5分毎 validtime（昇順）を返す。"""
    _, fcst = target_times()
    base = fcst[0]["basetime"]
    vts = sorted({r["validtime"] for r in fcst if r["basetime"] == base})
    return base, vts


def lonlat_to_tile(lon, lat, z=ZOOM):
    """緯度経度 → タイル座標(浮動小数)。標準の Web Mercator タイル方式。"""
    xtile = (lon + 180.0) / 360.0 * (2 ** z)
    lat_rad = math.radians(lat)
    ytile = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * (2 ** z)
    return xtile, ytile


def _tile_url(basetime, validtime, z, x, y):
    return f"{BASE}/jmatile/data/nowc/{basetime}/none/{validtime}/surf/hrpns/{z}/{x}/{y}.png"


def _load_tile(basetime, validtime, z, x, y):
    key = (basetime, validtime, z, x, y)
    if key not in _tile_cache:
        try:
            resp = get(_tile_url(basetime, validtime, z, x, y))
            _tile_cache[key] = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        except requests.HTTPError:
            _tile_cache[key] = None  # タイル範囲外（海外・データなし等）
    return _tile_cache[key]


def rain_at(lon, lat, basetime, validtime, z=ZOOM):
    """指定緯度経度・時刻の降雨を判定する。

    戻り値: {"raining": bool, "alpha": int(0-255), "rgb": (r,g,b) or None}
    タイルが存在しない（データ範囲外）場合は raining=False, rgb=None。
    """
    xt, yt = lonlat_to_tile(lon, lat, z)
    x, y = int(xt), int(yt)
    tile = _load_tile(basetime, validtime, z, x, y)
    if tile is None:
        return {"raining": False, "alpha": 0, "rgb": None}
    px = int((xt - x) * TILE_PX)
    py = int((yt - y) * TILE_PX)
    r, g, b, a = tile.getpixel((px, py))
    return {"raining": a > 0, "alpha": a, "rgb": (r, g, b)}


if __name__ == "__main__":
    obs, _ = target_times()
    print(f"実況フレーム数: {len(obs)}（最新 {obs[0]['basetime']}）")
    base, vts = current_forecast()
    print(f"予測の基準時刻: {base} / 予測フレーム数: {len(vts)}（{vts[0]} 〜 {vts[-1]}）")
    # 動作確認: 東京駅付近
    lon, lat = 139.7671, 35.6812
    for vt in [base] + vts[:3]:
        res = rain_at(lon, lat, base, vt)
        print(vt, res)
