# REST API for the platform. every endpoint returns JSON and hits real data.
import time
import threading

from flask import Blueprint, jsonify, request

from src import http_client
from src.analysis import cascades, reports, scenario
from src.ml import validate
from src.modules import MODULES, module_meta, runner
from src.modules.snapshot import EnvSnapshot
from src.pipeline import national, alerts
from src.services import usgs, noaa, nasa, geocoding

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
    # runner.assess falls back to the nearest scorable location before it gives
    # up, so a 502 here means a real outage rather than a coverage gap
    result = runner.assess(slug, snap)
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
    return jsonify({"knobs": scenario.KNOBS, "presets": scenario.PRESETS,
                    "bands": scenario.BANDS})


def _scenario_body():
    """shared parsing for the POST scenario endpoints"""
    body = request.get_json(silent=True) or {}
    try:
        lat, lon = float(body.get("lat")), float(body.get("lon"))
    except (TypeError, ValueError):
        return None, None
    return get_snapshot(lat, lon, body.get("name")), body


@api_bp.route("/scenario/run", methods=["POST"])
def scenario_run():
    snap, body = _scenario_body()
    if snap is None:
        return jsonify({"error": "lat and lon are required"}), 400
    out = scenario.run(snap, body.get("deltas") or {},
                       couple=body.get("couple", True))
    return jsonify(out), (200 if "error" not in out else 502)


@api_bp.route("/scenario/sensitivity", methods=["POST"])
def scenario_sensitivity():
    """which variable this location is most exposed to, ranked"""
    snap, body = _scenario_body()
    if snap is None:
        return jsonify({"error": "lat and lon are required"}), 400
    out = scenario.sensitivity(snap, body.get("deltas") or {})
    return jsonify(out), (200 if "error" not in out else 502)


@api_bp.route("/scenario/sweep", methods=["POST"])
def scenario_sweep():
    """response curve of every model against one knob"""
    snap, body = _scenario_body()
    if snap is None:
        return jsonify({"error": "lat and lon are required"}), 400
    out = scenario.sweep(snap, body.get("knob") or "temp_delta_c",
                         steps=int(body.get("steps") or 17),
                         deltas=body.get("deltas") or {})
    return jsonify(out), (200 if "error" not in out else 502)


@api_bp.route("/scenario/threshold", methods=["POST"])
def scenario_threshold():
    """how far a knob has to move before a hazard crosses a risk band"""
    snap, body = _scenario_body()
    if snap is None:
        return jsonify({"error": "lat and lon are required"}), 400
    out = scenario.threshold(snap, body.get("module") or "wildfire",
                             body.get("knob") or "temp_delta_c",
                             target=body.get("target") or "High",
                             deltas=body.get("deltas") or {})
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


@api_bp.route("/national/grid")
def national_grid():
    """the model-scored hazard field over every US state.

    unlike /live/heatmap, which only knows about places where something has
    already been reported, this covers the whole country: ~800 land points, each
    scored by the same trained models the module pages use. ?layer= picks the
    hazard, defaults to the composite."""
    try:
        day = int(request.args.get("day") or 0)
    except ValueError:
        day = 0
    data = national.layer(request.args.get("layer") or "composite", day=day)
    if data is None:
        # first ever request on a cold deploy: the build is running, say so
        # rather than returning an empty map that reads as "no data"
        return jsonify({"building": True, "status": national.status(),
                        "error": "The national grid is building for the first time. "
                                 "This takes a few minutes; the map fills in automatically."}), 202
    return jsonify(data)


@api_bp.route("/national/status")
def national_status():
    return jsonify(national.status())


@api_bp.route("/national/refresh", methods=["POST"])
def national_refresh():
    national.ensure(force=True)
    return jsonify(national.status()), 202


@api_bp.route("/alerts/check", methods=["POST"])
def alerts_check():
    """score a caller's watchlist against the national grid.

    the list is posted rather than stored: there are no accounts here, so the
    browser keeps the watchlist and the server keeps nothing about who asked."""
    body = request.get_json(silent=True) or {}
    watchlist = body.get("watchlist")
    if not isinstance(watchlist, list):
        return jsonify({"error": "watchlist must be a list of {name, lat, lon}"}), 400
    if len(watchlist) > 25:
        return jsonify({"error": "a watchlist is limited to 25 locations"}), 400
    return jsonify(alerts.evaluate(watchlist, body.get("threshold") or "High"))


@api_bp.route("/alerts/changes")
def alerts_changes():
    """what moved nationally between the two most recent grid builds"""
    try:
        limit = min(int(request.args.get("limit") or alerts.MAX_CHANGES), 100)
    except ValueError:
        limit = alerts.MAX_CHANGES
    return jsonify(alerts.changes(limit=limit))


@api_bp.route("/validation")
def model_validation():
    """cross-validated performance of the trained hazard models.

    served from a precomputed file: refitting five folds of two models takes
    about ten seconds, which is not a page load. `python -m src.ml.validate`
    regenerates it, and training already does."""
    rep = validate.load()
    if rep is None:
        return jsonify({"available": False,
                        "error": "No validation report has been generated yet. "
                                 "Run python -m src.ml.validate."}), 404
    return jsonify(rep)


@api_bp.route("/health")
def health():
    """cache and pipeline health, so a data problem is visible instead of
    showing up as mysteriously empty panels"""
    return jsonify({"http_cache": dict(http_client.stats),
                    "national_grid": national.status(),
                    "snapshots_cached": len(_snaps)})


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
