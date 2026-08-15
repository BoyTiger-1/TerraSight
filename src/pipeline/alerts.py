# watchlist alerts, evaluated against the national grid.
#
# the rest of the platform answers "what is the risk here, right now". that only
# helps someone who thought to look. this turns it around: name the places you
# care about once, and the platform tells you when one of them crosses a line.
#
# two things are computed here, from the same source:
#
#   watch      for a caller's own list of places, the current score of every
#              layer, which ones breach that caller's threshold, and how the
#              week ahead looks. no account needed: the list arrives in the
#              request, so it can live in the browser's localStorage.
#   changes    a national feed of what moved since the previous grid build.
#              this one does need memory, so each build's scores are kept in a
#              small history file and diffed against the one before.
#
# a crossing is only reported when a band boundary is passed, not on every
# wobble: 48 -> 52 is news because it entered High, 52 -> 58 is not.
import json
import os
import time

from src.pipeline import national
from src.services.nearest import haversine_km

HISTORY_FILE = os.path.join(national.CACHE_DIR, "national_history.json")

# how many past builds to keep. at the default 8-hour cadence this is two days,
# enough to say "this has been climbing since Tuesday" without growing forever.
HISTORY_KEEP = 6

# the bands the whole product speaks in, shared with the scenario simulator
BANDS = [(75.0, "Extreme"), (50.0, "High"), (25.0, "Moderate"), (0.0, "Low")]

# a change smaller than this is noise from the upstream forecast being refreshed,
# not a real move, so it never becomes an alert on its own
MIN_DELTA = 6.0

# how many national changes to report. the feed is meant to be read, not scrolled.
MAX_CHANGES = 25


def band(score):
    if score is None:
        return None
    for floor, name in BANDS:
        if score >= floor:
            return name
    return "Low"


def band_rank(name):
    order = ["Low", "Moderate", "High", "Extreme"]
    return order.index(name) if name in order else 0


# ---------------------------------------------------------------- watchlist

def _nearest_cell(grid, lat, lon):
    """the grid point covering a watched location.

    the national grid is a 1 degree lattice, so the nearest point can be up to
    about 80 km away. that is fine for a hazard field and wrong to hide, so the
    distance comes back with the answer."""
    best, best_km = None, None
    for r in grid["points"]:
        # cheap L1 prefilter: real distance only for genuinely close candidates
        if abs(r["lat"] - lat) + abs(r["lon"] - lon) > 4.0:
            continue
        km = haversine_km(lat, lon, r["lat"], r["lon"])
        if best_km is None or km < best_km:
            best, best_km = r, km
    return best, best_km


def evaluate(watchlist, threshold="High"):
    """score every watched location against every layer.

    watchlist: [{name, lat, lon, layers?, threshold?}, ...]. a location may
    override the global threshold and narrow the layers it cares about."""
    grid = national.ensure()
    if not grid:
        return {"building": True, "items": [], "alerts": []}

    dates = grid.get("days") or []
    items, alerts = [], []
    for entry in watchlist:
        try:
            lat, lon = float(entry["lat"]), float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        name = entry.get("name") or f"{lat:.2f}, {lon:.2f}"
        want = entry.get("layers") or [k for k in national.LAYERS if k != "composite"]
        limit = entry.get("threshold") or threshold
        floor = next((f for f, n in BANDS if n == limit), 50.0)

        cell, km = _nearest_cell(grid, lat, lon)
        if cell is None:
            items.append({"name": name, "lat": lat, "lon": lon,
                          "error": "outside the national grid"})
            continue

        layers, peak_day = {}, {}
        for key in national.LAYERS:
            series = [national.value_at(cell, key, d) for d in range(max(len(dates), 1))]
            series = [v for v in series if v is not None]
            if not series:
                continue
            layers[key] = {"now": series[0], "band": band(series[0]),
                           "week_peak": round(max(series), 1),
                           "series": series}
            peak_day[key] = series.index(max(series))

        breaches = []
        for key in want:
            info = layers.get(key)
            if not info:
                continue
            # the alert fires on the worst of the week, not only on today: being
            # told on Thursday that Thursday is dangerous is not much of a warning
            if info["week_peak"] >= floor:
                lead = peak_day.get(key, 0)
                breaches.append({
                    "layer": key, "label": national.LAYERS[key]["label"],
                    "now": info["now"], "peak": info["week_peak"],
                    "band": band(info["week_peak"]),
                    "lead_days": lead,
                    "date": dates[lead] if lead < len(dates) else None,
                    "already": info["now"] >= floor,
                })
        breaches.sort(key=lambda b: -b["peak"])

        item = {"name": name, "lat": lat, "lon": lon, "state": cell["state"],
                "distance_km": round(km, 1), "threshold": limit,
                "layers": layers, "breaches": breaches,
                "worst": breaches[0] if breaches else None,
                "composite": layers.get("composite", {}).get("now")}
        items.append(item)
        for b in breaches:
            alerts.append({**b, "name": name, "lat": lat, "lon": lon,
                           "state": cell["state"]})

    alerts.sort(key=lambda a: (-a["peak"], a["lead_days"]))
    items.sort(key=lambda i: -((i.get("worst") or {}).get("peak") or -1))
    return {"building": False, "items": items, "alerts": alerts,
            "days": dates, "generated_at": grid.get("generated_at"),
            "threshold": threshold, "layers": national.LAYERS}


# ---------------------------------------------------------------- history

def _load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"builds": []}


def record(grid):
    """append one build's scores to the history, keeping it small.

    only today's score per point per layer is kept: the diff is about what
    actually changed on the ground, and storing seven lead days per build would
    grow this file by an order of magnitude for a comparison nobody makes."""
    if not grid or not grid.get("points"):
        return
    snap = {"generated_at": grid.get("generated_at") or time.time(),
            "scores": {f"{r['lat']},{r['lon']}":
                       {k: r.get(k) for k in national.LAYERS if r.get(k) is not None}
                       for r in grid["points"]},
            "states": {s["state"]: s.get("composite")
                       for s in (grid.get("summary") or {}).get("states") or []}}
    hist = _load_history()
    builds = [b for b in hist.get("builds", [])
              if b.get("generated_at") != snap["generated_at"]]
    builds.append(snap)
    builds.sort(key=lambda b: b["generated_at"])
    hist["builds"] = builds[-HISTORY_KEEP:]
    national._write_json(HISTORY_FILE, hist)


def changes(limit=MAX_CHANGES):
    """what moved between the two most recent builds"""
    hist = _load_history()
    builds = hist.get("builds") or []
    if len(builds) < 2:
        return {"available": False, "builds": len(builds),
                "note": "The change feed compares consecutive builds of the "
                        "national grid, so it starts reporting after the second one.",
                "changes": [], "states": []}

    prev, cur = builds[-2], builds[-1]
    grid = national.ensure() or {}
    where = {f"{r['lat']},{r['lon']}": r for r in grid.get("points") or []}

    rows = []
    for key, now in cur["scores"].items():
        before = prev["scores"].get(key)
        if not before:
            continue
        for name, new_v in now.items():
            old_v = before.get(name)
            if old_v is None:
                continue
            delta = new_v - old_v
            if abs(delta) < MIN_DELTA:
                continue
            old_band, new_band = band(old_v), band(new_v)
            if old_band == new_band:
                continue    # a move inside one band is not a change of state
            cell = where.get(key) or {}
            rows.append({
                "lat": cell.get("lat"), "lon": cell.get("lon"),
                "state": cell.get("state"), "layer": name,
                "label": national.LAYERS[name]["label"],
                "from": old_v, "to": new_v, "delta": round(delta, 1),
                "from_band": old_band, "to_band": new_band,
                "direction": "up" if delta > 0 else "down",
                "rank": band_rank(new_band),
            })
    # the biggest escalations first, then the biggest de-escalations
    rows.sort(key=lambda r: (-(r["rank"] if r["direction"] == "up" else -1),
                             -abs(r["delta"])))

    states = []
    for name, now in (cur.get("states") or {}).items():
        old = (prev.get("states") or {}).get(name)
        if old is None or now is None or abs(now - old) < 2.0:
            continue
        states.append({"state": name, "from": old, "to": now,
                       "delta": round(now - old, 1)})
    states.sort(key=lambda s: -abs(s["delta"]))

    return {
        "available": True, "builds": len(builds),
        "since": prev["generated_at"], "until": cur["generated_at"],
        "hours": round((cur["generated_at"] - prev["generated_at"]) / 3600.0, 1),
        "escalations": sum(1 for r in rows if r["direction"] == "up"),
        "de_escalations": sum(1 for r in rows if r["direction"] == "down"),
        "changes": rows[:limit], "states": states[:12],
    }
