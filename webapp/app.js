// 濡れない経路デモ。地図操作はLeaflet、データはすべて同一オリジンの server.py 経由
// （JMAのJSONにCORSヘッダーが無くブラウザから直接fetchできないため、サーバー側で代行する）。
// 経路は地図クリックで複数の経由地を追加し、server.py が OSRM で道路にスナップした経路を返す。
let map, jmaLayer;
let waypointMarkers = [];      // クリックで追加した経由地（生の緯度経度）
let previewLine = null;        // クリック中の直線プレビュー
let roadLine = null;           // 評価後、実際に道路に沿った経路
let pointMarkers = [];         // 評価後の各点（濡れる/晴れ）マーカー
let frames = { base: null, validtimes: [] };
let latestResult = null;
let currentFrameIdx = 0;

function tileUrl(basetime, validtime) {
  return `https://www.jma.go.jp/bosai/jmatile/data/nowc/${basetime}/none/${validtime}/surf/hrpns/{z}/{x}/{y}.png`;
}

// JMAのhrpns(高解像度降水ナウキャスト)タイルは奇数ズーム(5,7,9)で空タイル(334byte)を返す
// （実測で確認）。ズームに応じた「有効な偶数ズーム」を1つだけ使うタイル層を作る。
// 注意: L.TileLayerの_getZoomForUrlをオーバーライドしてURLのzだけ変える方式は、
// タイル座標(x,y)は元のズーム用のまま残るため地理的に不整合なタイルを要求してしまい失敗する
// （実測で確認：z=7→z=6のURLに書き換えても表示は空白のままだった）。
// 正しい方法は、有効ズームが変わるたびに minNativeZoom=maxNativeZoom=そのズーム に固定した
// レイヤーを作り直すこと（Leaflet標準のスケール機構がx,y,zを整合させて処理してくれる）。
function effectiveNativeZoom(mapZoom) {
  let z = Math.min(10, Math.max(4, Math.round(mapZoom)));
  if (z % 2 !== 0) z -= 1;
  return z;
}

async function main() {
  map = L.map("map").setView([35.6812, 139.7671], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors", maxZoom: 18,
  }).addTo(map);

  map.on("click", onMapClick);
  map.on("zoomend", () => refreshJmaLayer());

  const res = await fetch("/api/frames");
  frames = await res.json();
  const slider = document.getElementById("frame-slider");
  slider.max = frames.validtimes.length; // 0=実況(base), 1..N=予測
  slider.value = 0;
  slider.addEventListener("input", () => updateFrame(parseInt(slider.value, 10)));
  updateFrame(0);

  document.getElementById("btn-eval").addEventListener("click", evaluate);
  document.getElementById("btn-clear").addEventListener("click", clearWaypoints);
}

function frameValidtime(idx) {
  return idx === 0 ? frames.base : frames.validtimes[idx - 1];
}

function formatJst(vt) {
  // "20260929021500" -> "02:15"
  return `${vt.slice(8, 10)}:${vt.slice(10, 12)}`;
}

function updateFrame(idx) {
  currentFrameIdx = idx;
  const vt = frameValidtime(idx);
  document.getElementById("frame-label").textContent =
    idx === 0 ? `実況 ${formatJst(vt)}` : `${idx * 5}分後 ${formatJst(vt)}`;
  refreshJmaLayer();
}

function refreshJmaLayer() {
  const vt = frameValidtime(currentFrameIdx);
  const eff = effectiveNativeZoom(map.getZoom());
  if (jmaLayer) map.removeLayer(jmaLayer);
  jmaLayer = L.tileLayer(tileUrl(frames.base, vt), {
    opacity: 0.7, minNativeZoom: eff, maxNativeZoom: eff, minZoom: 4, maxZoom: 18,
  }).addTo(map);
}

function onMapClick(e) {
  const idx = waypointMarkers.length + 1;
  const marker = L.marker(e.latlng, {
    icon: L.divIcon({ className: "waypoint-icon", html: `${idx}`, iconSize: [22, 22] }),
  }).addTo(map);
  waypointMarkers.push(marker);
  redrawPreview();
  updateWaypointUi();
}

function redrawPreview() {
  if (previewLine) map.removeLayer(previewLine);
  if (waypointMarkers.length >= 2) {
    previewLine = L.polyline(waypointMarkers.map((m) => m.getLatLng()), { color: "#a0aec0", dashArray: "6 6" }).addTo(map);
  }
}

function updateWaypointUi() {
  document.getElementById("waypoint-count").textContent = `経由地: ${waypointMarkers.length}点`;
  document.getElementById("btn-eval").disabled = waypointMarkers.length < 2;
}

function clearWaypoints() {
  waypointMarkers.forEach((m) => map.removeLayer(m));
  waypointMarkers = [];
  if (previewLine) { map.removeLayer(previewLine); previewLine = null; }
  if (roadLine) { map.removeLayer(roadLine); roadLine = null; }
  clearPointMarkers();
  updateWaypointUi();
  document.getElementById("result-summary").textContent = "経由地を指定して「評価」を押してください。";
  document.querySelector("#result-table tbody").innerHTML = "";
}

function clearPointMarkers() {
  pointMarkers.forEach((m) => map.removeLayer(m));
  pointMarkers = [];
}

async function evaluate() {
  const mode = document.getElementById("mode").value;
  const params = new URLSearchParams();
  waypointMarkers.forEach((m) => {
    const ll = m.getLatLng();
    params.append("points", `${ll.lat},${ll.lng}`);
  });
  params.append("mode", mode);
  document.getElementById("result-summary").textContent = "評価中…（道路経路を取得しています）";
  const res = await fetch(`/api/route?${params}`);
  const result = await res.json();
  if (result.error) {
    document.getElementById("result-summary").textContent = `エラー: ${result.error}`;
    return;
  }
  latestResult = result;
  renderResult(result);
}

function renderResult(result) {
  const best = result.best;
  const summary = document.getElementById("result-summary");
  const routing = result.used_road_routing ? "道路沿い（OSRM）" : "直線補間（道路経路が取得できず代替）";
  let text;
  if (best.wet_ratio === 0) {
    text = `${best.depart_offset_min}分後に出発すれば、経路上で雨に当たらない見込みです。`;
  } else if (best.wet_ratio < 0.5) {
    text = `どの時間帯でも完全には避けられませんが、${best.depart_offset_min}分後の出発が最も濡れにくい見込みです（${best.wet_points}/${best.total_points}点で降雨）。`;
  } else {
    text = `⚠️ この60分間はいずれの出発時刻でも経路の半分以上で雨に当たる見込みです。${best.depart_offset_min}分後が相対的には最良ですが、それでも${best.wet_points}/${best.total_points}点で降雨が見込まれます。`;
  }
  text += ` [経路: ${routing}、距離${result.distance_m}m]`;
  summary.textContent = text;

  const tbody = document.querySelector("#result-table tbody");
  tbody.innerHTML = "";
  result.evals.forEach((e) => {
    const tr = document.createElement("tr");
    // 「最良」でも比率が高いままなら緑(=安全)に見せない。比率に応じて色を変える。
    if (e.depart_offset_min === best.depart_offset_min) {
      if (best.wet_ratio === 0) tr.classList.add("best-dry");
      else if (best.wet_ratio < 0.5) tr.classList.add("best-partial");
      else tr.classList.add("best-wet");
    }
    tr.innerHTML = `<td>${e.depart_offset_min}分後</td><td>${e.wet_points}/${e.total_points}</td><td>${Math.round(e.wet_ratio * 100)}%</td>`;
    tr.addEventListener("click", () => showPoints(e));
    tbody.appendChild(tr);
  });

  // 道路に沿った実際の経路ラインを描画
  if (roadLine) map.removeLayer(roadLine);
  roadLine = L.polyline(result.geometry.map((p) => [p.lat, p.lon]), { color: "#2b6cb0", weight: 4 }).addTo(map);

  showPoints(best);
}

function showPoints(evalRow) {
  clearPointMarkers();
  evalRow.points.forEach((p) => {
    const marker = L.circleMarker([p.lat, p.lon], {
      radius: 6, color: "#fff", weight: 1,
      fillColor: p.raining ? "#c53030" : "#2f855a", fillOpacity: 0.9,
    }).addTo(map);
    marker.bindTooltip(`${p.raining ? "☔ 雨" : "☀ 晴れ"}（到達 ${p.eta.slice(11, 16)}）`);
    pointMarkers.push(marker);
  });
}

main();
