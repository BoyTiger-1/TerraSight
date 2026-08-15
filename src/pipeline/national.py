# the national model pipeline.
#
# the command-center heatmap used to be built only from *reported events*, so it
# lit up wherever something had already happened and left most of the country
# dark. that is a reporting map, not a risk map. this module runs the actual
# model stack over a regular grid covering all fifty states, so every square of
# the USA carries a score whether or not anything is burning there today.
#
# the flow, end to end:
#   grid      src/data/us_grid.py lays ~800 points over US land
#   fetch     Open-Meteo bulk forecast, batched and rate-paced (see BUDGET below)
#   features  the same src/ml/features.py code the per-location modules use
#   models    the trained wildfire and flood classifiers score every point
#   indices   heat, drought, wind and winter layers from documented thresholds
#   serve     cached on disk, refreshed on a background thread, served instantly
#
# BUDGET. Open-Meteo's free tier does not count HTTP requests, it counts
# locations x variables x days. a 92-day pull over 821 points costs about 5,800
# units, which blows the 5,000/hour ceiling in a single build and truncated the
# first version of this pipeline to a fifth of the country. so the fetch is
# split in two:
#
#   fine tier    every point, 35 days back + 7 forecast. enough for the flood
#                model, heat, wind, winter, and 30-day fire dryness. ~3 units
#                per point, refreshed a few times a day.
#   memory tier  a coarse 2.5 degree grid, 92 days back, precipitation and
#                evaporation only. supplies the 90-day rainfall memory the
#                wildfire model needs. seasonal totals vary smoothly enough that
#                nearest-neighbour interpolation onto the fine grid is fair, and
#                it only has to be refreshed daily.
import json
import math
import os
import threading
import time

from src import config, http_client
from src.data import us_grid
from src.ml import features as F
from src.ml.registry import get_model
from src.services import open_meteo
from src.services.nearest import haversine_km

# coordinates per HTTP request. 60 keeps each response a few hundred KB, which
# is comfortably inside every proxy and gateway timeout we might sit behind.
BATCH = 60

# Open-Meteo bills roughly locations x ceil(days / 14). the free tier allows 600
# units a minute and 5,000 an hour; pace well under both so a build never gets
# silently truncated and normal user traffic still has headroom.
UNITS_PER_MINUTE = 380
UNITS_PER_HOUR = 4200   # the real ceiling is 5,000; leave room for user traffic

# Open-Meteo bills by 14-day blocks, so 32 + 10 costs exactly what 35 + 7 did:
# ceil(42/14) = 3 units per point. trading three days of history for three more
# forecast days buys the whole outlook animation for nothing. 32 days still
# covers every backward window the scoring uses (the longest is 30).
FINE_PAST_DAYS = 32
FINE_FORECAST_DAYS = 10

# how many days the national outlook animates. day 0 is today. the cap is set by
# the forward-looking indices: wind and winter read a window ahead of the day
# they score, and at lead 6 there are still four forecast days left to read.
OUTLOOK_DAYS = 7
OUTLOOK_WINDOW = 4      # forward days every lead day gets, so leads compare fairly

MEMORY_PAST_DAYS = 92
MEMORY_SPACING_DEG = 2.5
MEMORY_TTL = 22 * 3600      # the 90-day rainfall memory only moves day to day

# how long a built grid is considered current. weather models update hourly but
# a national risk field does not meaningfully move in less than a few hours, and
# a slower cadence keeps us well inside the free API's daily budget.
REFRESH_SECONDS = int(os.environ.get("TERRASIGHT_GRID_REFRESH", 8 * 3600))

CACHE_DIR = os.environ.get(
    "TERRASIGHT_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 ".cache"))
CACHE_FILE = os.path.join(CACHE_DIR, "national_grid.json")
MEMORY_FILE = os.path.join(CACHE_DIR, "national_memory.json")

DAILY_VARS = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum",
              "wind_speed_10m_max", "wind_gusts_10m_max",
              "et0_fao_evapotranspiration", "snowfall_sum", "relative_humidity_2m_min"]
MEMORY_VARS = ["precipitation_sum", "et0_fao_evapotranspiration"]

# the layers the API can serve, and how each one is produced. "model" means a
# trained classifier scores it; "index" means a documented threshold formula.
LAYERS = {
    "composite":  {"label": "Composite hazard",  "method": "blend"},
    "wildfire":   {"label": "Wildfire",          "method": "model"},
    "flood":      {"label": "Flood",             "method": "model"},
    "heat":       {"label": "Extreme heat",      "method": "index"},
    "drought":    {"label": "Drought stress",    "method": "index"},
    "wind":       {"label": "Damaging wind",     "method": "index"},
    "winter":     {"label": "Winter storm",      "method": "index"},
}

# how much each hazard contributes to the composite. wildfire and flood lead
# because they are the two model-backed layers.
COMPOSITE_WEIGHTS = {"wildfire": 1.0, "flood": 1.0, "heat": 0.85,
                     "drought": 0.6, "wind": 0.7, "winter": 0.7}


# ---------------------------------------------------------------- helpers

def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _scale(value, lo, hi):
    if value is None or hi == lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _percentile(values, q):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def _sum(vals):
    return sum(v for v in vals if v is not None)


def _window_max(vals):
    clean = [v for v in vals if v is not None]
    return max(clean) if clean else 0.0


def _units(locations, days):
    """Open-Meteo's approximate call weight for one bulk request"""
    return locations * max(1, math.ceil(days / 14))


class _Pacer:
    """spread request weight so we trip neither the per-minute nor the hourly
    ceiling. the hourly one is the binding constraint for a full national build,
    and it is the one that silently truncated the early versions of this job."""

    def __init__(self, units_per_minute=UNITS_PER_MINUTE, units_per_hour=UNITS_PER_HOUR):
        self.per_unit = 60.0 / max(units_per_minute, 1)
        self.next_at = 0.0
        self.hour_cap = units_per_hour
        self.spent = []          # (timestamp, units) over the trailing hour

    def budget_left(self):
        cutoff = time.time() - 3600
        self.spent = [s for s in self.spent if s[0] > cutoff]
        return self.hour_cap - sum(u for _, u in self.spent)

    def wait(self, units):
        """returns False when the hourly budget cannot cover this request, so
        the caller can stop cleanly rather than collect a few hundred 429s"""
        if self.budget_left() < units:
            return False
        now = time.time()
        if now < self.next_at:
            time.sleep(self.next_at - now)
            now = time.time()
        self.next_at = now + self.per_unit * units
        self.spent.append((now, units))
        return True


# ---------------------------------------------------------------- indices
# each of these mirrors the logic of the matching per-location module, reduced
# to what a single grid point's daily series can support. the module pages stay
# the authoritative deep-dive; these are the national field.

def _heat_index(frame, i):
    """today's heat against this point's own recent-season distribution.
    absolute danger and local anomaly both matter: 38 C is routine in Phoenix
    and an emergency in Seattle, and the percentile term is what captures that."""
    tmax = frame["tmax"][i]
    if tmax is None:
        return None
    season = frame["tmax"][max(0, i - 35):i]
    p90 = _percentile(season, 0.90)
    anomaly = (tmax - p90) if p90 is not None else 0.0
    absolute = _scale(tmax, 28.0, 45.0)
    relative = _scale(anomaly, 0.0, 6.0)
    tmin = frame["tmin"][i]
    # nights that stay hot are what turn a hot day into a health emergency
    night = _scale(tmin, 20.0, 30.0) if tmin is not None else 0.0
    return _clamp((0.45 * absolute + 0.40 * relative + 0.15 * night) * 100)


def _drought_index(frame, i, memory):
    """evaporative demand against rainfall supply, plus the dry-spell clock.
    the 90-day term comes from the memory tier, the 30-day term is local."""
    precip_30 = _sum(frame["precip"][max(0, i - 29):i + 1])
    et0_30 = _sum(frame["et0"][max(0, i - 29):i + 1])
    ratio_30 = et0_30 / (precip_30 + 10.0)
    deficit_90 = (memory or {}).get("deficit_90_mm")
    dry_days = 0
    for j in range(i, -1, -1):
        if (frame["precip"][j] or 0) >= 2.5:
            break
        dry_days += 1
    parts = [(0.40, _scale(ratio_30, 0.8, 8.0)), (0.25, _scale(dry_days, 7, 35))]
    if deficit_90 is not None:
        parts.append((0.35, _scale(deficit_90, 0, 450)))
    weight = sum(w for w, _ in parts)
    return _clamp(sum(w * v for w, v in parts) / weight * 100)


def _wind_index(frame, i, window=OUTLOOK_WINDOW):
    """strongest gust in the window ahead, on the scale NWS uses for wind
    advisories (about 74 km/h) and high wind warnings (about 93 km/h).

    the window is the same length at every lead day. a fixed window is what makes
    the outlook animation honest: if day 0 looked four days ahead and day 6 only
    one, the map would fade out toward the end of the week purely as an artifact
    of running out of forecast, and look like the weather was calming down."""
    gusts = frame["gust_max"][i:i + window] or frame["gust_max"][i:i + 1]
    peak = _window_max(gusts)
    sustained = _window_max(frame["wind_max"][i:i + window])
    return _clamp((0.75 * _scale(peak, 55.0, 130.0) +
                   0.25 * _scale(sustained, 35.0, 90.0)) * 100)


def _winter_index(frame, i, window=OUTLOOK_WINDOW):
    """snowfall accumulation in the window ahead combined with cold and wind,
    which is what separates a snowfall from a blizzard"""
    snow = _sum(frame["snowfall"][i:i + window])
    tmin = min([v for v in frame["tmin"][i:i + window] if v is not None] or [10])
    gust = _window_max(frame["gust_max"][i:i + window])
    if snow < 0.5 and tmin > -12:
        return 0.0
    return _clamp((0.55 * _scale(snow, 2.0, 45.0) +
                   0.25 * _scale(-tmin, 5.0, 30.0) +
                   0.20 * _scale(gust, 40.0, 90.0)) * 100)


# ---------------------------------------------------------------- memory tier

def _build_memory(pacer, progress=None):
    """coarse 90-day rainfall and evaporation totals, the fire model's memory"""
    points = us_grid.build(MEMORY_SPACING_DEG)
    out = []
    for start in range(0, len(points), BATCH):
        chunk = points[start:start + BATCH]
        pacer.wait(_units(len(chunk), MEMORY_PAST_DAYS))
        responses = open_meteo.forecast_multi(
            [(p["lat"], p["lon"]) for p in chunk], daily=MEMORY_VARS,
            past_days=MEMORY_PAST_DAYS, forecast_days=1)
        for point, resp in zip(chunk, responses):
            d = (resp or {}).get("daily") or {}
            precip = d.get("precipitation_sum") or []
            et0 = d.get("et0_fao_evapotranspiration") or []
            if len(precip) < 60:
                continue
            p90 = _sum(precip[:MEMORY_PAST_DAYS])
            e90 = _sum(et0[:MEMORY_PAST_DAYS])
            out.append({"lat": point["lat"], "lon": point["lon"],
                        "precip_90_mm": round(p90, 1),
                        "deficit_90_mm": round(e90 - p90, 1)})
        if progress:
            progress("memory", min(start + BATCH, len(points)), len(points))
    return {"generated_at": time.time(), "spacing_deg": MEMORY_SPACING_DEG, "points": out}


def _load_memory():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as fh:
            mem = json.load(fh)
        if time.time() - mem.get("generated_at", 0) < MEMORY_TTL and mem.get("points"):
            return mem
    except Exception:
        pass
    return None


def _memory_lookup(memory):
    """nearest coarse cell for any fine-grid point. the coarse grid is small
    enough (a couple of hundred cells) that a linear scan is instant."""
    pts = (memory or {}).get("points") or []
    if not pts:
        return lambda lat, lon: None

    def find(lat, lon):
        best, best_d = None, 1e9
        for p in pts:
            d = abs(p["lat"] - lat) + abs(p["lon"] - lon)  # cheap L1 prefilter
            if d < best_d:
                best, best_d = p, d
        if best is None:
            return None
        km = haversine_km(lat, lon, best["lat"], best["lon"])
        return {**best, "distance_km": round(km, 1)}
    return find


# ---------------------------------------------------------------- scoring

def _score_day(frame, i, memory):
    """every hazard layer for one day of one point's series"""
    if frame["tmax"][i] is None:
        return None
    scores = {}

    wf_model = get_model("wildfire")
    if wf_model:
        feats = _wildfire_features(frame, i, memory)
        if feats:
            scores["wildfire"] = round(wf_model.predict(feats) * 100, 1)

    fl_model = get_model("flood")
    fl_feats = F.flood_features(frame, i)
    if fl_model and fl_feats:
        scores["flood"] = round(fl_model.predict(fl_feats) * 100, 1)

    for key, fn in (("heat", _heat_index), ("wind", _wind_index), ("winter", _winter_index)):
        v = fn(frame, i)
        if v is not None:
            scores[key] = round(v, 1)
    scores["drought"] = round(_drought_index(frame, i, memory), 1)

    # composite: the worst hazard dominates, but a second elevated hazard should
    # still raise the square. a plain max hides compound risk and a plain mean
    # buries a single extreme, so take the weighted worst plus a share of the rest.
    weighted = sorted(((scores[k] * COMPOSITE_WEIGHTS.get(k, 0.5), k)
                       for k in scores), reverse=True)
    if weighted:
        top = weighted[0][0]
        rest = sum(v for v, _ in weighted[1:])
        scores["composite"] = round(_clamp(top + 0.10 * rest), 1)
    return scores


def score_point(resp, memory=None):
    """turn one Open-Meteo response into every hazard layer for that point, for
    today and for each of the next OUTLOOK_DAYS - 1 days.

    the forecast days cost nothing extra: they are already in the response the
    fine tier pays for, they were simply being thrown away. scoring them is pure
    CPU and turns a snapshot of the country into a week of it."""
    frame = F.daily_frame(resp)
    if not frame or not frame.get("time"):
        return None
    # with past_days=FINE_PAST_DAYS the series starts that many days back, so
    # today sits at exactly that index
    i = min(FINE_PAST_DAYS, len(frame["time"]) - 1)
    if i < 0:
        return None

    today = _score_day(frame, i, memory)
    if today is None:
        return None

    # a lead day is only scored if the whole comparison window is present, so a
    # short response drops the tail of the animation instead of quietly scoring
    # the last days against less data than the first.
    outlook, days = {k: [v] for k, v in today.items()}, [frame["time"][i]]
    for d in range(1, OUTLOOK_DAYS):
        j = i + d
        if j + OUTLOOK_WINDOW > len(frame["time"]):
            break
        scores = _score_day(frame, j, memory)
        if scores is None:
            break
        days.append(frame["time"][j])
        for k in outlook:
            outlook[k].append(scores.get(k))

    context = {
        "tmax_c": frame["tmax"][i],
        "precip_7d_mm": round(_sum(frame["precip"][i:i + 7]), 1),
        "gust_max_kmh": round(_window_max(frame["gust_max"][i:i + 7]), 1),
    }
    return {"scores": today, "outlook": outlook, "days": days, "context": context}


def _wildfire_features(frame, i, memory):
    """the trained model's feature vector, with the 90-day rainfall term taken
    from the memory tier since the fine tier only carries 35 days"""
    tmax = frame["tmax"][i]
    rh = frame["rh_min"][i]
    if tmax is None:
        return None
    rh = 40.0 if rh is None else rh
    precip_7 = _sum(frame["precip"][max(0, i - 6):i + 1])
    precip_30 = _sum(frame["precip"][max(0, i - 29):i + 1])
    et0_7 = _sum(frame["et0"][max(0, i - 6):i + 1])
    et0_30 = _sum(frame["et0"][max(0, i - 29):i + 1])
    # without the coarse memory tier, extrapolate the 30-day total. that is a
    # weaker estimate, so it is only a fallback, never the normal path.
    precip_90 = (memory or {}).get("precip_90_mm")
    if precip_90 is None:
        precip_90 = precip_30 * 3.0
    days_since_rain = 0
    for j in range(i, -1, -1):
        if (frame["precip"][j] or 0) >= 2.5:
            break
        days_since_rain += 1
    tmax_7 = [v for v in frame["tmax"][max(0, i - 6):i + 1] if v is not None]
    return {
        "tmax_c": round(tmax, 2),
        "rh_min_pct": round(rh, 1),
        "wind_max_kmh": round(frame["wind_max"][i] or 0, 1),
        "gust_max_kmh": round(frame["gust_max"][i] or 0, 1),
        "precip_7d_mm": round(precip_7, 1),
        "precip_30d_mm": round(precip_30, 1),
        "precip_90d_mm": round(precip_90, 1),
        "days_since_rain": days_since_rain,
        "et0_7d_mm": round(et0_7, 1),
        "vpd_kpa": round(F.vapor_pressure_deficit(tmax, rh), 3),
        "dryness_ratio": round(et0_30 / (precip_30 + 10.0), 3),
        "tmax_7d_mean": round(sum(tmax_7) / len(tmax_7), 2) if tmax_7 else round(tmax, 2),
    }


# ---------------------------------------------------------------- the build

API_HOST = config.OPEN_METEO_FORECAST   # the host whose quota gates this job


def _fetch_batches(points, pacer, lookup, progress=None, done=0, total=None):
    """returns (scored_rows, failed_points, stopped_early)"""
    rows, failed = [], []
    total = total or len(points)
    for start in range(0, len(points), BATCH):
        chunk = points[start:start + BATCH]

        # if the host has already told us we are over an hourly quota, every
        # further request is a guaranteed 429. bail out and let the caller keep
        # whatever the previous build produced for these points.
        if http_client.rate_limited_for(API_HOST) > 120:
            failed.extend(points[start:])
            return rows, failed, True
        if not pacer.wait(_units(len(chunk), FINE_PAST_DAYS + FINE_FORECAST_DAYS)):
            failed.extend(points[start:])
            return rows, failed, True

        coords = [(p["lat"], p["lon"]) for p in chunk]
        responses = open_meteo.forecast_multi(
            coords, daily=DAILY_VARS, past_days=FINE_PAST_DAYS,
            forecast_days=FINE_FORECAST_DAYS)
        if not any(responses) and http_client.rate_limited_for(API_HOST) <= 120:
            # a whole batch coming back empty with no hourly block in force means
            # the minute budget is spent, not that the country vanished. wait for
            # the window to roll over and take one more swing.
            time.sleep(62)
            responses = open_meteo.forecast_multi(
                coords, daily=DAILY_VARS, past_days=FINE_PAST_DAYS,
                forecast_days=FINE_FORECAST_DAYS)
        for point, resp in zip(chunk, responses):
            scored = score_point(resp, lookup(point["lat"], point["lon"])) if resp else None
            if not scored:
                failed.append(point)
                continue
            rows.append({"lat": point["lat"], "lon": point["lon"],
                         "state": point["state"], **scored["scores"],
                         "o": scored["outlook"], "days": scored["days"],
                         "ctx": scored["context"]})
        if progress:
            progress("grid", done + min(start + BATCH, len(points)), total)
    return rows, failed, False


def _carry_forward(failed, previous):
    """reuse the last good score for any point this pass could not reach.

    a national map that loses a third of its points because the free tier's
    hourly quota ran out is worse than one carrying a few hours of extra age, so
    the build tops up the previous result rather than replacing it. each carried
    row is stamped with its own age and counted separately in the status."""
    if not previous:
        return [], len(failed)
    index = {(round(r["lat"], 3), round(r["lon"], 3)): r for r in previous.get("points") or []}
    prev_age = previous.get("generated_at") or time.time()
    carried, still_missing = [], 0
    for p in failed:
        row = index.get((round(p["lat"], 3), round(p["lon"], 3)))
        if row is None:
            still_missing += 1
            continue
        # a carried row's outlook is anchored to the previous build's calendar,
        # so its "day 3" is not this build's day 3. keeping today's score is a
        # fair few-hours-stale reading; keeping a misaligned week is not, so the
        # outlook is dropped and the animation simply skips this point.
        carried.append({k: v for k, v in row.items() if k != "o"}
                       | {"stale_since": row.get("stale_since", prev_age)})
    return carried, still_missing


def build(progress=None):
    """fetch and score the whole national grid. blocking, takes a few minutes.

    this is deliberately incremental: whatever the current pass cannot reach
    keeps its previous score, so repeated passes converge on full coverage even
    when a single pass cannot fit inside the free tier's hourly budget."""
    started = time.time()
    pacer = _Pacer()
    previous = _load_cache()

    memory = _load_memory()
    if memory is None:
        memory = _build_memory(pacer, progress)
        if memory:
            _write_json(MEMORY_FILE, memory)
    lookup = _memory_lookup(memory)

    points = us_grid.build()
    rows, failed, stopped = _fetch_batches(points, pacer, lookup, progress, total=len(points))

    # one retry sweep for anything the first pass missed, but only if we were
    # not stopped by a quota: retrying into a closed door just wastes minutes
    if failed and not stopped:
        time.sleep(5)
        retry_rows, failed, stopped = _fetch_batches(failed, pacer, lookup, progress,
                                                     done=len(points), total=len(points))
        rows.extend(retry_rows)

    fresh_count = len(rows)
    carried, still_missing = _carry_forward(failed, previous)
    rows.extend(carried)
    rows.sort(key=lambda r: (r["lat"], r["lon"]))

    # every point shares the same forecast window, so the day labels are a
    # property of the build, not of each of 821 rows. hoist them out and drop the
    # duplicates, which keeps the cache file about a fifth smaller.
    days = max((r.pop("days", None) or [] for r in rows), key=len, default=[])

    return {
        "days": days,
        "outlook_days": len(days),
        "generated_at": time.time(),
        "spacing_deg": us_grid.SPACING_CONUS,
        "requested_points": len(points),
        "scored_points": len(rows),
        "fresh_points": fresh_count,
        "carried_points": len(carried),
        "missing_points": still_missing,
        "complete": still_missing == 0,
        "quota_limited": stopped,
        "retry_after": round(http_client.rate_limited_for(API_HOST)),
        "memory_points": len((memory or {}).get("points") or []),
        "build_seconds": round(time.time() - started, 1),
        "layers": LAYERS,
        "points": rows,
        "summary": summarize(rows),
        # one summary per lead day so the side panel animates with the map
        "summaries": [summarize(rows, d) for d in range(len(days))],
    }


def value_at(row, name, day=0):
    """one layer's score for one point on one lead day.

    day 0 always reads the plain field, so anything written before the outlook
    existed, and any carried-forward row, still answers for today."""
    if day <= 0:
        return row.get(name)
    series = (row.get("o") or {}).get(name)
    if not series or day >= len(series):
        return None
    return series[day]


def summarize(rows, day=0):
    """per-state means and the national hotspots, for the dashboard side panel"""
    by_state = {}
    for r in rows:
        vals = {name: value_at(r, name, day) for name in LAYERS}
        if all(v is None for v in vals.values()):
            continue
        acc = by_state.setdefault(r["state"], {"n": 0, "sums": {}})
        acc["n"] += 1
        for name, v in vals.items():
            if v is not None:
                acc["sums"][name] = acc["sums"].get(name, 0.0) + v

    states = []
    for name, acc in by_state.items():
        means = {k: round(v / acc["n"], 1) for k, v in acc["sums"].items()}
        states.append({"state": name, "points": acc["n"], **means})
    states.sort(key=lambda s: -(s.get("composite") or 0))

    scored = [(value_at(r, "composite", day), r) for r in rows]
    hot = sorted(((v, r) for v, r in scored if v is not None), key=lambda p: -p[0])[:12]
    national = {}
    for name in LAYERS:
        vals = [v for v in (value_at(r, name, day) for r in rows) if v is not None]
        if vals:
            national[name] = round(sum(vals) / len(vals), 1)

    def driver(row):
        cands = [(value_at(row, k, day), k) for k in LAYERS if k != "composite"]
        cands = [c for c in cands if c[0] is not None]
        return max(cands)[1] if cands else None

    return {"day": day, "states": states, "national_mean": national,
            "points": sum(a["n"] for a in by_state.values()),
            "hotspots": [{"lat": h["lat"], "lon": h["lon"], "state": h["state"],
                          "composite": v, "driver": driver(h)} for v, h in hot]}


# ---------------------------------------------------------------- serving

_state = {"grid": None, "building": False, "error": None, "phase": None,
          "progress": (0, 0), "resume_at": None, "grid_mtime": 0.0}
_state_lock = threading.Lock()


def _write_json(path, payload):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        pass


def _load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _disk_mtime():
    try:
        return os.path.getmtime(CACHE_FILE)
    except OSError:
        return 0.0


def _synced_grid():
    """the newest grid we know of. call with _state_lock held.

    a build can finish in another process (an offline top-up run, or a second
    worker) and land on disk while this one is still holding an older pass in
    memory. comparing mtimes means whoever closes the coverage gap, everybody
    serves the result."""
    mtime = _disk_mtime()
    if _state["grid"] is None or mtime > _state["grid_mtime"]:
        fresh = _load_cache()
        if fresh is not None:
            _state["grid"] = fresh
            _state["grid_mtime"] = mtime
    return _state["grid"]


def _progress(phase, done, total):
    with _state_lock:
        _state["phase"] = phase
        _state["progress"] = (done, total)


def _refresh():
    try:
        grid = build(progress=_progress)
        if grid["scored_points"] == 0:
            raise RuntimeError("no grid points could be scored")
        _write_json(CACHE_FILE, grid)
        with _state_lock:
            _state["grid"] = grid
            _state["grid_mtime"] = _disk_mtime()
            _state["error"] = None

        # remember this build so the change feed has something to diff against.
        # imported here rather than at module scope because alerts reads from
        # this module, and a top-level import either way would be circular.
        from src.pipeline import alerts
        alerts.record(grid)

        # a pass cut short by the hourly quota leaves gaps that the next pass can
        # close. schedule that pass for just after the quota resets so coverage
        # converges on its own instead of waiting for the next 8-hour refresh.
        if grid.get("quota_limited") and not grid.get("complete"):
            delay = max(grid.get("retry_after") or 0, 60) + 30
            with _state_lock:
                _state["resume_at"] = time.time() + delay
            t = threading.Timer(delay, _resume)
            t.daemon = True
            t.start()
    except Exception as e:
        with _state_lock:
            _state["error"] = f"{type(e).__name__}: {e}"
    finally:
        with _state_lock:
            _state["building"] = False
            _state["phase"] = None


def _resume():
    with _state_lock:
        if _state["building"]:
            return
        _state["building"] = True
        _state["resume_at"] = None
    _refresh()


def ensure(force=False):
    """return the current grid, kicking off a background rebuild if it is stale.

    never blocks on the network: a stale grid is served immediately while the
    fresh one is built, and the very first request gets whatever is on disk."""
    with _state_lock:
        grid = _synced_grid()
        stale = grid is None or (time.time() - grid.get("generated_at", 0)) > REFRESH_SECONDS
        if (stale or force) and not _state["building"]:
            _state["building"] = True
            threading.Thread(target=_refresh, name="national-grid", daemon=True).start()
        return grid


def status():
    with _state_lock:
        # read-only peek at the disk cache. unlike ensure() this never kicks off
        # a build, so polling /health or /national/status cannot accidentally
        # start one.
        grid = _synced_grid()
        done, total = _state["progress"]
        return {
            "building": _state["building"], "phase": _state["phase"],
            "progress": {"done": done, "total": total},
            "error": _state["error"],
            "has_grid": grid is not None,
            "generated_at": grid.get("generated_at") if grid else None,
            "age_seconds": round(time.time() - grid["generated_at"]) if grid else None,
            "scored_points": grid.get("scored_points") if grid else 0,
            "requested_points": grid.get("requested_points") if grid else 0,
            "fresh_points": grid.get("fresh_points") if grid else 0,
            "carried_points": grid.get("carried_points") if grid else 0,
            "missing_points": grid.get("missing_points") if grid else 0,
            "complete": bool(grid.get("complete")) if grid else False,
            "quota_limited": bool(grid.get("quota_limited")) if grid else False,
            "resume_in": (round(_state["resume_at"] - time.time())
                          if _state["resume_at"] else None),
            "coverage_pct": (round(100.0 * grid.get("scored_points", 0)
                                   / max(grid.get("requested_points", 1), 1), 1)
                             if grid else 0),
            "refresh_seconds": REFRESH_SECONDS,
        }


def layer(name="composite", day=0):
    """[[lat, lon, weight 0-1], ...] ready for the leaflet heat layer, plus the
    raw cells so the map can also draw a crisp grid instead of a blur.

    day selects a lead day from the outlook. selecting server-side keeps the
    response the same size it has always been: the client asks for the frame it
    is about to draw instead of downloading the whole week up front."""
    grid = ensure()
    if not grid:
        return None
    name = name if name in LAYERS else "composite"
    dates = grid.get("days") or []
    day = max(0, min(int(day or 0), max(len(dates) - 1, 0)))

    points, cells = [], []
    for r in grid["points"]:
        v = value_at(r, name, day)
        if v is None:
            continue
        points.append([r["lat"], r["lon"], round(v / 100.0, 3)])
        cell = {"lat": r["lat"], "lon": r["lon"], "state": r["state"], "v": v}
        if day == 0:
            # the context block describes current conditions, so it only belongs
            # on today's frame. sending it with every frame would triple the
            # payload of an animation that never shows it.
            cell["ctx"] = r.get("ctx", {})
        cells.append(cell)

    summaries = grid.get("summaries") or []
    summary = summaries[day] if day < len(summaries) else grid.get("summary", {})
    return {
        "layer": name, "label": LAYERS[name]["label"], "method": LAYERS[name]["method"],
        "spacing_deg": grid.get("spacing_deg"), "generated_at": grid.get("generated_at"),
        "age_seconds": round(time.time() - grid.get("generated_at", time.time())),
        "day": day, "date": dates[day] if day < len(dates) else None, "days": dates,
        "count": len(points), "points": points, "cells": cells,
        "coverage": {"scored": grid.get("scored_points"),
                     "requested": grid.get("requested_points"),
                     "missing": grid.get("missing_points", 0),
                     "carried_points": grid.get("carried_points", 0),
                     "complete": grid.get("complete", False)},
        "summary": summary, "available_layers": LAYERS,
    }


if __name__ == "__main__":  # python -m src.pipeline.national
    def show(phase, done, total):
        print(f"  {phase}: {done}/{total}   ", end="\r", flush=True)
    print("building the national grid...")
    g = build(progress=show)
    _write_json(CACHE_FILE, g)
    # a command line build is still a build, so it belongs in the change history
    # the same way the scheduled refresh does
    from src.pipeline import alerts
    alerts.record(g)
    print(f"\nscored {g['scored_points']}/{g['requested_points']} points "
          f"(fresh {g['fresh_points']}, carried {g['carried_points']}, "
          f"{g['missing_points']} missing) in {g['build_seconds']}s")
    if g["quota_limited"]:
        print(f"  stopped by the hourly API quota, retry in {g['retry_after']}s "
              f"to fill the remaining gaps")
    for s in g["summary"]["states"][:8]:
        print(f"  {s['state']:22s} composite {s.get('composite')}")
