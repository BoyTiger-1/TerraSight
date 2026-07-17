# the scenario simulator: take real current conditions as a baseline, let the
# user turn the knobs (temperature, rain, wind, snow...), and rerun every
# simulatable model instantly. each module keeps its own feature dict, the
# same knob transforms apply to each, so "humidity" in the fire model never
# collides with "humidity" in the tornado model.
import copy

from src.ml.features import vapor_pressure_deficit
from src.modules import MODULES

# what the UI can adjust, with sane bounds
KNOBS = {
    "temp_delta_c": {"label": "Temperature shift", "min": -8, "max": 8, "step": 0.5, "unit": "C", "default": 0},
    "precip_mult": {"label": "Precipitation multiplier", "min": 0, "max": 3, "step": 0.1, "unit": "x", "default": 1},
    "wind_mult": {"label": "Wind multiplier", "min": 0.3, "max": 2.5, "step": 0.1, "unit": "x", "default": 1},
    "rh_delta_pct": {"label": "Humidity shift", "min": -30, "max": 30, "step": 1, "unit": "%", "default": 0},
    "snow_mult": {"label": "Snowpack multiplier", "min": 0, "max": 3, "step": 0.1, "unit": "x", "default": 1},
    "sst_delta_c": {"label": "Sea surface temp shift", "min": -3, "max": 4, "step": 0.5, "unit": "C", "default": 0},
}


def baseline(snap):
    """per-module features + scores from a full assessment pass"""
    envs, scores = {}, {}
    for slug, meta in MODULES.items():
        if not meta.get("simulatable"):
            continue
        try:
            r = meta["impl"].assess(snap)
        except Exception:
            continue
        if "error" in r:
            continue
        envs[slug] = r.get("features") or {}
        scores[slug] = r["assessment"]["score"]
    return envs, scores


def apply_deltas(feats, deltas):
    """push the knob settings through one module's feature dict.
    physics notes: ET0 demand rises ~5%/C, CAPE ~7%/C (Clausius-Clapeyron),
    climatological baselines like tmax_p90 stay fixed, that is the what-if."""
    e = copy.deepcopy(feats)
    dt = float(deltas.get("temp_delta_c") or 0)
    pm = 1.0 if deltas.get("precip_mult") is None else float(deltas["precip_mult"])
    wm = 1.0 if deltas.get("wind_mult") is None else float(deltas["wind_mult"])
    drh = float(deltas.get("rh_delta_pct") or 0)
    sm = 1.0 if deltas.get("snow_mult") is None else float(deltas["snow_mult"])
    dsst = float(deltas.get("sst_delta_c") or 0)

    for k in ["tmax_c", "tmax_7d_mean", "tmin_c"]:
        if e.get(k) is not None:
            e[k] += dt

    if e.get("rh_min_pct") is not None:
        e["rh_min_pct"] = max(3.0, min(100.0, e["rh_min_pct"] + drh))

    for k in ["precip_1d_mm", "precip_3d_mm", "precip_7d_mm", "precip_30d_mm",
              "precip_90d_mm", "precip_max1d_30d_mm", "api_index"]:
        if e.get(k) is not None:
            e[k] *= pm
    if e.get("wet_days_30d") is not None:
        e["wet_days_30d"] = min(30, round(e["wet_days_30d"] * (pm ** 0.5)))
    if e.get("days_since_rain") is not None:
        if pm <= 0.05:
            e["days_since_rain"] = 90
        elif pm < 0.5:
            e["days_since_rain"] = min(90, round(e["days_since_rain"] * 1.8 + 3))
        elif pm > 1.5:
            e["days_since_rain"] = max(0, round(e["days_since_rain"] / 2))

    for k in ["wind_max_kmh", "gust_max_kmh"]:
        if e.get(k) is not None:
            e[k] *= wm

    for k in ["snowfall_3d_cm", "snow_depth_cm", "snowfall_30d_cm"]:
        if e.get(k) is not None:
            e[k] *= sm

    if e.get("sst_c") is not None:
        e["sst_c"] += dsst
    if e.get("storm_intensity_kt") is not None:
        e["wind_mult"] = wm  # cyclone quick() scales the storm with the wind knob

    # derived fields recomputed from the shifted state
    if e.get("tmax_c") is not None and e.get("rh_min_pct") is not None:
        e["vpd_kpa"] = round(vapor_pressure_deficit(e["tmax_c"], e["rh_min_pct"]), 3)
    if e.get("dryness_ratio") is not None:
        # clamp to the envelope seen in training: beyond ~15 the model has only
        # seen fuel-limited deserts and the trees stop being meaningful
        e["dryness_ratio"] = min(e["dryness_ratio"] * (1 + 0.05 * dt) / max(pm, 0.08), 15.0)
    if e.get("et0_7d_mm") is not None:
        e["et0_7d_mm"] *= (1 + 0.05 * dt)
    if e.get("cape") is not None:
        e["cape"] = max(0, e["cape"] * (1 + 0.07 * dt))
    if e.get("hot_days") is not None and dt:
        # warmer scenario pushes more forecast days over the fixed p90 bar
        e["hot_days"] = max(0, min(7, round(e["hot_days"] + dt * 1.2)))

    return e


# the baseline (real conditions) is expensive to build, but it does not change
# while a user drags sliders, so cache it per location for 10 minutes. this is
# what makes the simulator feel instant: only quick() re-scoring runs per drag.
import time as _time
_baseline_cache = {}


def cached_baseline(snap):
    key = (round(snap.lat, 3), round(snap.lon, 3))
    hit = _baseline_cache.get(key)
    if hit and _time.time() - hit[0] < 600:
        return hit[1]
    result = baseline(snap)
    _baseline_cache[key] = (_time.time(), result)
    if len(_baseline_cache) > 50:
        for k in sorted(_baseline_cache, key=lambda k: _baseline_cache[k][0])[:25]:
            _baseline_cache.pop(k, None)
    return result


def run(snap, deltas):
    """baseline vs modified scores for every simulatable module"""
    envs, base_scores = cached_baseline(snap)
    if not envs:
        return {"error": "Could not build a baseline for this location."}

    rows = []
    modified_all = {}
    for slug, feats in envs.items():
        quick = getattr(MODULES[slug]["impl"], "quick", None)
        if not quick:
            continue
        modified = apply_deltas(feats, deltas)
        modified_all[slug] = modified
        try:
            # score both states through quick() so alert-driven floors in the
            # full assessment cannot distort the before/after comparison
            before = quick(feats)
            after = quick(modified)
        except Exception:
            before = after = None
        if before is None:
            before = base_scores.get(slug)
        if before is None or after is None:
            continue
        rows.append({"module": slug, "title": MODULES[slug]["title"],
                     "before": round(before, 1), "after": round(after, 1),
                     "change": round(after - before, 1)})
    rows.sort(key=lambda r: -abs(r["change"]))

    def _clean(d):
        return {k: (round(v, 2) if isinstance(v, float) else v)
                for k, v in d.items() if v is not None and not isinstance(v, dict)}

    return {"baseline_env": {s: _clean(f) for s, f in envs.items()},
            "modified_env": {s: _clean(f) for s, f in modified_all.items()},
            "results": rows, "knobs": KNOBS}
