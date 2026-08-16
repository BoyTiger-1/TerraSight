# running a module against a location, with the guarantee the product makes:
# a location always gets an answer.
#
# four layers of defence, in order of how much they cost:
#   1. every feed inside EnvSnapshot already falls back to the nearest point
#      that has coverage (see snapshot._resolve)
#   2. http_client serves the last good response when an upstream is throttled
#   3. if a module still cannot score this exact point, we re-run it against a
#      snapshot anchored a short distance away and label the result as a
#      neighbouring-location estimate
#   4. when the upstream is throttled outright, so that no amount of walking will
#      help, we read the hazard off the pre-computed national grid instead
#
# only after all four fail does a module report an error, and by then it really
# is an outage rather than a coverage gap.
import threading
import time

from src import config, http_client
from src.config import risk_band
from src.modules import MODULES
from src.services.nearest import haversine_km, ring

# how far to step out when the point itself cannot be scored. these are small on
# purpose: a hazard score 40 km away is a fair proxy, one 400 km away is not.
FALLBACK_RADII_KM = (30, 75, 150)

# the feed nearly every module depends on. when its quota is spent, a neighbour
# will fail for exactly the same reason, so walking outward is pure waste.
_PRIMARY_FEED = config.OPEN_METEO_FORECAST

_neighbor_cache = {}
_neighbor_lock = threading.Lock()


def _neighbor_snapshot(lat, lon, name):
    from src.modules.snapshot import EnvSnapshot
    key = (round(lat, 2), round(lon, 2))
    with _neighbor_lock:
        snap = _neighbor_cache.get(key)
        if snap is None:
            snap = EnvSnapshot(lat, lon, name)
            _neighbor_cache[key] = snap
            if len(_neighbor_cache) > 120:
                _neighbor_cache.clear()
        return snap


# which national-grid layer stands in for which module. the grid is built by the
# same models on the same feeds, so these are the same numbers at coarser
# resolution -- not a different opinion. the other seven modules have no grid
# layer: agriculture, avalanche, landslide and tornado are not modelled
# nationally, and air-quality, earthquake, cyclone, tsunami, volcano and
# infrastructure do not depend on the weather feed that runs out of quota.
GRID_LAYER_FOR = {
    "wildfire": "wildfire", "flood": "flood", "drought": "drought",
    "heatwave": "heat", "winter": "winter",
}

# past this the grid is describing weather that has since moved on. a day-old
# national picture is a fair answer during an outage; a four-day-old one is not.
GRID_MAX_AGE_SECONDS = 36 * 3600


def _grid_estimate(slug, snap):
    """score one module off the national grid, or None if it cannot.

    this is what keeps the site useful through an upstream outage. the grid is
    already on disk, costs nothing to read, and covers the whole country, so
    while the live feed is closed it can still answer for the five hazards it
    models -- clearly labelled, at lower confidence, and never silently."""
    layer = GRID_LAYER_FOR.get(slug)
    if layer is None:
        return None
    from src.pipeline import national

    grid = national.peek()
    if not grid:
        return None
    age = time.time() - (grid.get("generated_at") or 0)
    if age > GRID_MAX_AGE_SECONDS:
        return None
    row, km = national.nearest_point(snap.lat, snap.lon)
    if row is None:
        return None
    score = national.value_at(row, layer)
    if score is None:
        return None

    hours = int(age // 3600)
    label, color = risk_band(float(score))
    # a 1-degree national pass is a real answer but a blunter one than a
    # point-resolution run, and it gets blunter the further the cell is: inside
    # the mainland lattice the nearest node is always close, but for an island or
    # the far north it can be the next landmass over, which is a much weaker
    # claim and should not be presented as an equally good one.
    confidence = 0.45 if km <= 90 else 0.3 if km <= 180 else 0.2
    return {
        "module": slug,
        "location": {"name": snap.name, "lat": snap.lat, "lon": snap.lon},
        "assessment": {
            "score": round(float(score), 1), "level": label, "color": color,
            "confidence": confidence, "confidence_label": "Low",
            "kind": MODULES[slug].get("kind", "prediction"),
            "headline": (
                f"Live feed unavailable, so this is read off the national hazard "
                f"model: {label.lower()} risk ({float(score):.0f}/100) for the "
                f"1-degree cell {km:.0f} km away, computed {hours}h ago."
            ),
        },
        "factors": [], "features": {}, "recommendations": [],
        "data_sources": ["TerraSight national hazard grid (1-degree, daily)"],
        "methodology": (
            "Fallback path. The per-location feed was rate limited, so the score "
            "comes from the most recent national grid build, which runs the same "
            "model over a 1-degree lattice of the United States."
        ),
        "data_provenance": {
            "national_grid_fallback": {
                "status": "grid", "layer": layer, "distance_km": round(km, 1),
                "age_hours": hours, "cell": [row["lat"], row["lon"]],
            },
        },
    }


def _mark_substituted(result, snap, distance_km, source):
    """tag a result that was produced somewhere other than the requested point"""
    result["location"] = {"name": snap.name, "lat": snap.lat, "lon": snap.lon}
    prov = result.setdefault("data_provenance", {})
    prov["substituted_location"] = {
        "status": "nearest", "distance_km": round(distance_km, 1),
        "source": [round(source[0], 4), round(source[1], 4)],
    }
    a = result.get("assessment") or {}
    # a proxy reading is genuinely less certain than a local one
    a["confidence"] = round(max(0.15, (a.get("confidence") or 0.5) * 0.8), 2)
    a["confidence_label"] = ("High" if a["confidence"] >= 0.75
                             else "Medium" if a["confidence"] >= 0.5 else "Low")
    a["headline"] = (f"Modelled from the nearest location with coverage, about "
                     f"{distance_km:.0f} km away. " + (a.get("headline") or ""))
    result["assessment"] = a
    return result


def assess(slug, snap, allow_fallback=True):
    """score one module for one location, substituting a neighbour if needed"""
    impl = MODULES[slug]["impl"]
    try:
        result = impl.assess(snap)
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    if "error" not in result or not allow_fallback:
        return result

    first_error = result["error"]

    # some failures are properties of the server, not of the point. an unloadable
    # model returns the same error at every latitude on earth, so walking twelve
    # neighbours only spends a dozen snapshots' worth of API quota to be told the
    # same thing twelve more times.
    if result.get("model_missing"):
        return result

    # the neighbour sweep costs a dozen fresh snapshots. that is worth it for a
    # genuine coverage gap, but not when the upstream is simply throttled: every
    # neighbour would hit the same closed door, twelve times over per module.
    limited_for = http_client.rate_limited_for(_PRIMARY_FEED)
    if limited_for > 0:
        # the walk is pointless, but the national grid is already on disk and
        # was built by these same models, so for the hazards it covers we can
        # still say something true instead of nothing at all.
        est = _grid_estimate(slug, snap)
        if est is not None:
            return est
        # tell the caller how long this actually lasts. a free-tier daily quota
        # runs until UTC midnight, and inviting someone to hit Retry against a
        # door that stays shut for another eight hours is worse than saying so.
        return {"error": first_error, "rate_limited": True,
                "retry_after": round(limited_for)}

    for radius in FALLBACK_RADII_KM:
        for clat, clon in ring(snap.lat, snap.lon, radius, count=4):
            near = _neighbor_snapshot(clat, clon, snap.name)
            try:
                alt = impl.assess(near)
            except Exception:
                continue
            if "error" in alt:
                continue
            return _mark_substituted(alt, snap, haversine_km(snap.lat, snap.lon, clat, clon),
                                     (clat, clon))
    return {"error": first_error}


def assess_all(snap):
    """every module for one location, each with the same fallback guarantee"""
    return {slug: assess(slug, snap) for slug in MODULES}
