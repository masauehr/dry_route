"""ID-15 検証: 「濡れない経路・出発時刻」を、気象庁の高解像度降水ナウキャストで評価する。

経路は2つ以上の経由地（緯度経度）を指定する。OSRM（openstreetmap.de の公開デモサーバー、
認証不要・OpenStreetMapデータ）で道路に沿った実際の経路を取得し、取得できない場合のみ
経由地間の直線で代替する（海上等でルートが見つからない場合のフォールバック）。
出発時刻を5分ずつ遅らせた候補それぞれについて、経路上の各点をその通過予定時刻の
ナウキャスト画像で調べ、「濡れる点の割合」が最小の出発時刻を推す。

出典: 気象庁ホームページ（高解像度降水ナウキャスト）・OpenStreetMap（ルーティング、道路データ）
"""
import math
import sys
from datetime import datetime, timedelta

import requests

import fetch_nowcast as fn

WALK_MPS = 4.8 * 1000 / 3600   # 徒歩 4.8km/h
BIKE_MPS = 15.0 * 1000 / 3600  # 自転車 15km/h

# openstreetmap.de（FOSSGIS）が公開しているOSRMデモサーバー。認証不要・OSMデータ。
# 商用・大量アクセス不可、デモ用途のみ（利用規約: https://routing.openstreetmap.de/ ）。
OSRM_BASE = {
    "walk": "https://routing.openstreetmap.de/routed-foot/route/v1/foot/",
    "bike": "https://routing.openstreetmap.de/routed-bike/route/v1/bike/",
}
UA = {"User-Agent": "weather-hackathon-spike (personal research)"}


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_road_route(waypoints, mode):
    """waypoints: [(lat,lon), ...]（2点以上）。OSRMで道路に沿った経路を取得する。

    戻り値: ([(lat,lon), ...], distance_m) または、取得できなければ None。
    """
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url = OSRM_BASE[mode] + coord_str
    try:
        r = requests.get(url, params={"overview": "full", "geometries": "geojson"}, headers=UA, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok":
            return None
        route = data["routes"][0]
        coords = [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]
        return coords, route["distance"]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def resample_polyline(coords, n_points=20):
    """折れ線（経路ジオメトリ、道路沿いでも直線区間でもよい）を、距離で等間隔の n_points 点に再サンプルする。"""
    dists = [0.0]
    for i in range(1, len(coords)):
        dists.append(dists[-1] + haversine_m(*coords[i - 1], *coords[i]))
    total = dists[-1]
    pts = []
    for i in range(n_points):
        target = total * i / (n_points - 1) if n_points > 1 else 0
        j = 0
        while j < len(dists) - 2 and dists[j + 1] < target:
            j += 1
        seg_len = dists[j + 1] - dists[j]
        t = 0 if seg_len == 0 else (target - dists[j]) / seg_len
        lat = coords[j][0] + (coords[j + 1][0] - coords[j][0]) * t
        lon = coords[j][1] + (coords[j + 1][1] - coords[j][1]) * t
        pts.append({"lat": lat, "lon": lon, "dist_m": target})
    return pts, total


def _parse(vt):
    return datetime.strptime(vt, "%Y%m%d%H%M%S")


def _fmt(dt):
    return dt.strftime("%Y%m%d%H%M%S")


def nearest_validtime(target_dt, available_vts, base_dt):
    """target_dt 以前で最も近い利用可能な validtime を返す（無ければ最新の実況=baseを使う）。"""
    candidates = [v for v in available_vts if _parse(v) <= target_dt]
    if not candidates:
        return _fmt(base_dt)
    return max(candidates, key=_parse)


def evaluate(route_pts, speed_mps, depart_offset_min, base, vts):
    """出発を depart_offset_min 分後にした場合の、経路各点の濡れ判定を返す。"""
    base_dt = _parse(base)
    depart_dt = base_dt + timedelta(minutes=depart_offset_min)
    results = []
    for pt in route_pts:
        elapsed_s = pt["dist_m"] / speed_mps
        eta = depart_dt + timedelta(seconds=elapsed_s)
        vt = nearest_validtime(eta, vts, base_dt)
        r = fn.rain_at(pt["lon"], pt["lat"], base, vt)
        results.append({**pt, "eta": eta, "validtime": vt, **r})
    wet = sum(1 for r in results if r["raining"])
    return {"depart_offset_min": depart_offset_min, "wet_points": wet, "total_points": len(results),
            "wet_ratio": wet / len(results), "points": results}


MAX_DETOUR_RATIO = 3.0  # OSRM経路が直線距離の何倍を超えたら「迂回しすぎ」とみなし直線にフォールバックするか


def recommend(waypoints, speed_mps, mode="walk", n_points=20, max_wait_min=60):
    """waypoints: [(lat,lon), ...]（2点以上、経由地順）。"""
    straight_dist_m = sum(
        haversine_m(*waypoints[i], *waypoints[i + 1]) for i in range(len(waypoints) - 1)
    )
    road = fetch_road_route(waypoints, mode)
    # OSRMが極端に迂回した経路（山道の周回等）を返すことがあるため、直線距離の
    # MAX_DETOUR_RATIO倍を超えたら信頼せず直線にフォールバックする。
    if road and straight_dist_m > 0 and road[1] > straight_dist_m * MAX_DETOUR_RATIO:
        road = None
    used_road_routing = road is not None
    geometry = road[0] if road else waypoints
    route_pts, total_dist_m = resample_polyline(geometry, n_points)

    base, vts = fn.current_forecast()
    step = 5
    offsets = list(range(0, max_wait_min + 1, step))
    evals = [evaluate(route_pts, speed_mps, off, base, vts) for off in offsets]
    best = min(evals, key=lambda e: (e["wet_ratio"], e["depart_offset_min"]))
    return {
        "base": base, "distance_m": round(total_dist_m), "evals": evals, "best": best,
        "used_road_routing": used_road_routing,
        "geometry": [{"lat": lat, "lon": lon} for lat, lon in geometry],
    }


def main():
    # デモ: 東京駅 → 皇居外苑（約1.7km、道路沿い）。実況で雨が観測されている地点で検証。
    waypoints = [(35.6812, 139.7671), (35.6852, 139.7528)]  # 東京駅 → 皇居外苑あたり
    mode = sys.argv[1] if len(sys.argv) > 1 else "walk"
    speed = WALK_MPS if mode == "walk" else BIKE_MPS

    result = recommend(waypoints, speed, mode)
    routing = "道路沿い(OSRM)" if result["used_road_routing"] else "直線補間(フォールバック)"
    print(f"基準時刻(実況): {result['base']} / 距離: {result['distance_m']}m / 移動手段: {mode} / 経路: {routing}")
    print(f"{'出発(分後)':>10} {'濡れる点':>8} {'/':1}{'全点':>4} {'比率':>6}")
    for e in result["evals"]:
        mark = " ←最良" if e is result["best"] else ""
        print(f"{e['depart_offset_min']:>10} {e['wet_points']:>8} / {e['total_points']:>4} {e['wet_ratio']*100:>5.0f}%{mark}")

    best = result["best"]
    if best["wet_ratio"] == 0:
        print(f"\n→ {best['depart_offset_min']}分後に出発すれば、経路上で雨に当たらない見込みです。")
    else:
        print(f"\n→ どの時間帯でも完全には避けられませんが、{best['depart_offset_min']}分後の出発が最も濡れにくい見込みです"
              f"（{best['wet_points']}/{best['total_points']}点で降雨）。")


if __name__ == "__main__":
    main()
