# REST API for the platform. every endpoint returns JSON and hits real data.
import time
import threading

from flask import Blueprint, jsonify, request

from src.analysis import cascades, reports, scenario
from src.modules import MODULES, module_meta
from src.modules.snapshot import EnvSnapshot
from src.services import open_meteo, usgs, noaa, nasa, geocoding

api_bp = Blueprint("api", __name__)

# snapshots are expensive to warm up, so reuse them for 10 minutes per location
_snaps = {}
_snap_lock = threading.Lock()


def get_snapshot(lat, lon, name=None):
    key = (round(float(lat), 3), round(float(lon), 3))
    now = time.time()
    with _snap_lock:
        hit = _snaps.get(key)
        if hit and now - hit[0] < 600:
            snap = hit[1]
            if name:
                snap.name = name
            return snap
        snap = EnvSnapshot(lat, lon, name)
        _snaps[key] = (now, snap)
        if len(_snaps) > 60:
            for k in sorted(_snaps, key=lambda k: _snaps[k][0])[:30]:
                _snaps.pop(k, None)
        return snap


def _coords():
    """pull lat/lon/name off the query string, raising a clean 400 on junk"""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return None, None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None, None
    return lat, lon, request.args.get("name")


@api_bp.route("/modules")
def modules():
    return jsonify(module_meta())


@api_bp.route("/geocode")
def geocode():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    # merged city + street-address search
    return jsonify(geocoding.search(q))


@api_bp.route("/reverse-geocode")
def reverse_geocode():
    """clicked map point -> readable place name"""
    lat, lon, _ = _coords()
    if lat is None:
        return jsonify({"error": "Valid lat and lon are required"}), 400
    result = geocoding.reverse(lat, lon)
    return jsonify(result or {"name": f"{lat:.3f}, {lon:.3f}", "lat": lat, "lon": lon})


@api_bp.route("/assess/<slug>")
def assess(slug):
    if slug not in MODULES:
        return jsonify({"error": f"Unknown module '{slug}'"}), 404
    lat, lon, name = _coords()
    if lat is None:
        return jsonify({"error": "Valid lat and lon query parameters are required"}), 400
    snap = get_snapshot(lat, lon, name)
    try:
        result = MODULES[slug]["impl"].assess(snap)
    except Exception as e:
        return jsonify({"error": f"Assessment failed: {type(e).__name__}: {e}"}), 500
    status = 200 if "error" not in result else 502
    return jsonify(result), status


@api_bp.route("/assess-all")
def assess_all():
    lat, lon, name = _coords()
    if lat is None:
        return jsonify({"error": "Valid lat and lon query parameters are required"}), 400
    snap = get_snapshot(lat, lon, name)
    return jsonify(cascades.run_all(snap))


@api_bp.route("/report")
def report():
    lat, lon, name = _coords()
    if lat is None:
        return jsonify({"error": "Valid lat and lon query parameters are required"}), 400
    snap = get_snapshot(lat, lon, name)
    return jsonify(reports.generate(snap))


@api_bp.route("/scenario/knobs")
def scenario_knobs():
    return jsonify(scenario.KNOBS)


@api_bp.route("/scenario/run", methods=["POST"])
def scenario_run():
    body = request.get_json(silent=True) or {}
    try:
        lat, lon = float(body.get("lat")), float(body.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400
    snap = get_snapshot(lat, lon, body.get("name"))
    out = scenario.run(snap, body.get("deltas") or {})
    return jsonify(out), (200 if "error" not in out else 502)


@api_bp.route("/live/heatmap")
def live_heatmap():
    """dense worldwide activity for the global risk heatmap: every recent
    earthquake shapes the seismic belts, plus live fires, storms, and volcanoes.
    each point carries a 0-1 weight and a hazard kind for filtering."""
    points = []

    # global seismicity, 60 days of M4+ traces the plate boundaries clearly
    for q in usgs.earthquakes(days=60, min_magnitude=4.0, limit=2000):
        if q.get("lat") is None or q.get("magnitude") is None:
            continue
        # magnitude 4 -> ~0.35, magnitude 8 -> 1.0, energy grows fast
        w = max(0.3, min((q["magnitude"] - 3.0) / 5.0, 1.0))
        points.append({"lat": q["lat"], "lon": q["lon"], "kind": "earthquake", "weight": round(w, 2)})

    # every open natural event worldwide
    kind_map = {"wildfires": "wildfire", "severeStorms": "storm", "volcanoes": "volcano",
                "floods": "flood"}
    for e in nasa.eonet_events(days=30, limit=400):
        if e.get("lat") is None:
            continue
        kind = kind_map.get(e["category"], "other")
        points.append({"lat": e["lat"], "lon": e["lon"], "kind": kind, "weight": 0.55})

    # active tropical cyclones, weighted by wind
    for s in noaa.active_storms():
        if s.get("lat") is None:
            continue
        w = max(0.4, min((s.get("intensity_kt") or 40) / 140.0, 1.0))
        points.append({"lat": s["lat"], "lon": s["lon"], "kind": "storm", "weight": round(w, 2)})

    # elevated volcanoes
    for v in usgs.volcanoes():
        if v.get("lat") is None or (v.get("alert_level") or "NORMAL").upper() == "NORMAL":
            continue
        points.append({"lat": v["lat"], "lon": v["lon"], "kind": "volcano", "weight": 0.7})

    counts = {}
    for p in points:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    return jsonify({"points": points, "counts": counts, "total": len(points)})


@api_bp.route("/live/overview")
def live_overview():
    """the global situation strip: active events, storms, big quakes"""
    events = nasa.eonet_events(days=20, limit=200) or []
    storms = noaa.active_storms() or []
    quakes = usgs.earthquakes(days=7, min_magnitude=4.5, limit=100) or []
    by_cat = {}
    for e in events:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
    return jsonify({
        "eonet_events": events,
        "eonet_by_category": by_cat,
        "active_storms": storms,
        "significant_quakes": quakes,
        "counts": {"events": len(events), "storms": len(storms), "quakes": len(quakes)},
    })
