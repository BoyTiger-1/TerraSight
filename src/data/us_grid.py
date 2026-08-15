# the national sampling grid: every point the model pipeline scores so the
# heatmap covers all fifty states instead of only wherever an event happened to
# be reported today.
#
# us_states.json is a simplified boundary set (50 states + DC + Puerto Rico).
# we lay a regular lat/lon lattice over the country's bounding boxes and keep
# the points that fall inside a state polygon, which gives full land coverage
# with no wasted API calls over open ocean or Canada.
import json
import os
import threading

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_states.json")

# grid spacing in degrees. CONUS gets the fine spacing; Alaska is enormous and
# sparsely populated so it gets a coarser one, which keeps the total point count
# (and therefore the API budget) sane.
SPACING_CONUS = float(os.environ.get("TERRASIGHT_GRID_DEG", "1.0"))
ALASKA_FACTOR = 2.5

# states that need their own spacing or sit outside the CONUS bounding box
COARSE_STATES = {"Alaska"}

_lock = threading.Lock()
_cached_grids = {}
_cached_polys = None


# ---------------------------------------------------------------- geometry

def _polygons():
    """[(state_name, [ring, ...]), ...] where a ring is [[lon, lat], ...]"""
    global _cached_polys
    if _cached_polys is not None:
        return _cached_polys
    with open(_DATA, encoding="utf-8") as fh:
        fc = json.load(fh)
    polys = []
    for feat in fc["features"]:
        name = feat["properties"]["name"]
        geom = feat["geometry"]
        parts = ([geom["coordinates"]] if geom["type"] == "Polygon"
                 else geom["coordinates"])
        for part in parts:
            outer = part[0]
            lons = [c[0] for c in outer]
            lats = [c[1] for c in outer]
            # cache each part's bbox: the bbox rejects 99% of candidates for the
            # cost of four comparisons, so point-in-polygon runs rarely
            polys.append({"state": name, "ring": outer,
                          "bbox": (min(lons), min(lats), max(lons), max(lats))})
    _cached_polys = polys
    return polys


def _point_in_ring(lon, lat, ring):
    """standard ray casting; ring is a closed list of [lon, lat]"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def locate(lat, lon):
    """which US state contains this point, or None if it is outside the US"""
    for p in _polygons():
        minx, miny, maxx, maxy = p["bbox"]
        if not (minx <= lon <= maxx and miny <= lat <= maxy):
            continue
        if _point_in_ring(lon, lat, p["ring"]):
            return p["state"]
    return None


# ---------------------------------------------------------------- the grid

def _frange(lo, hi, step):
    out, v = [], lo
    while v <= hi + 1e-9:
        out.append(round(v, 4))
        v += step
    return out


def build(spacing=None):
    """the national point list at a given spacing, cached after the first call.

    each point is {lat, lon, state}. building it is pure CPU, about a second,
    and the result never changes, so it is computed once per spacing."""
    spacing = SPACING_CONUS if spacing is None else float(spacing)
    with _lock:
        if spacing in _cached_grids:
            return _cached_grids[spacing]

        # index polygons by state so we can pick spacing per state and skip the
        # bounding-box scan of the whole country for every candidate
        by_state = {}
        for p in _polygons():
            by_state.setdefault(p["state"], []).append(p)

        points = []
        seen = set()
        for state, parts in by_state.items():
            step = spacing * ALASKA_FACTOR if state in COARSE_STATES else spacing
            minx = min(p["bbox"][0] for p in parts)
            miny = min(p["bbox"][1] for p in parts)
            maxx = max(p["bbox"][2] for p in parts)
            maxy = max(p["bbox"][3] for p in parts)
            # Alaska crosses the antimeridian in the source data; clamp to the
            # western hemisphere slice, which is where the land actually is
            if state == "Alaska":
                minx = max(minx, -170.0)
                maxx = min(maxx, -130.0)
            for lat in _frange(miny, maxy, step):
                for lon in _frange(minx, maxx, step):
                    key = (round(lat, 3), round(lon, 3))
                    if key in seen:
                        continue
                    if not any(_point_in_ring(lon, lat, p["ring"]) for p in parts):
                        continue
                    seen.add(key)
                    points.append({"lat": lat, "lon": lon, "state": state})

        # small states can fall entirely between grid lines at 1 degree spacing.
        # every state must appear in a national map, so anything that produced
        # no point gets its polygon centroid instead.
        covered = {p["state"] for p in points}
        for state, parts in by_state.items():
            if state in covered:
                continue
            biggest = max(parts, key=lambda p: len(p["ring"]))
            ring = biggest["ring"]
            clat = sum(c[1] for c in ring) / len(ring)
            clon = sum(c[0] for c in ring) / len(ring)
            points.append({"lat": round(clat, 4), "lon": round(clon, 4), "state": state})

        points.sort(key=lambda p: (p["lat"], p["lon"]))
        _cached_grids[spacing] = points
        return points


def stats():
    pts = build()
    by_state = {}
    for p in pts:
        by_state[p["state"]] = by_state.get(p["state"], 0) + 1
    return {"points": len(pts), "states": len(by_state),
            "spacing_deg": SPACING_CONUS, "by_state": by_state}


if __name__ == "__main__":  # python -m src.data.us_grid
    s = stats()
    print(f"{s['points']} grid points across {s['states']} states "
          f"at {s['spacing_deg']} degree spacing")
    for k, v in sorted(s["by_state"].items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {k:24s} {v}")
