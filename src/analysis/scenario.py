# the scenario simulator: take real current conditions as a baseline, let the
# user turn the knobs (temperature, rain, wind, snow, soil, instability...), and
# rerun every simulatable model instantly. each module keeps its own feature
# dict, the same knob transforms apply to each, so "humidity" in the fire model
# never collides with "humidity" in the tornado model.
#
# on top of the plain before/after run there are three analyses that turn the
# toy into something you can actually reason with:
#   sensitivity  sweep every knob one at a time, rank which variable this
#                location is most exposed to
#   sweep        the full response curve of every model against one knob
#   threshold    solve for the knob value where a hazard crosses a risk band
import copy
import time as _time

from src.analysis import cascades
from src.ml.features import vapor_pressure_deficit
from src.modules import MODULES

# what the UI can adjust, with sane bounds. `group` drives the section headers
# in the simulator panel.
KNOBS = {
    "temp_delta_c": {"label": "Temperature shift", "min": -8, "max": 8, "step": 0.5,
                     "unit": "C", "default": 0, "group": "Atmosphere",
                     "help": "shifts daily highs, lows, and wind chill together"},
    "precip_mult": {"label": "Precipitation multiplier", "min": 0, "max": 3, "step": 0.1,
                    "unit": "x", "default": 1, "group": "Water",
                    "help": "scales every rainfall window and the dry-spell clock"},
    "rh_delta_pct": {"label": "Humidity shift", "min": -30, "max": 30, "step": 1,
                     "unit": "%", "default": 0, "group": "Atmosphere",
                     "help": "minimum relative humidity, the fine-fuel driver"},
    "wind_mult": {"label": "Wind multiplier", "min": 0.3, "max": 2.5, "step": 0.1,
                  "unit": "x", "default": 1, "group": "Atmosphere",
                  "help": "sustained wind and gusts"},
    "soil_moisture_delta": {"label": "Soil moisture shift", "min": -0.15, "max": 0.15,
                            "step": 0.01, "unit": "m3/m3", "default": 0, "group": "Water",
                            "help": "volumetric water content in the root zone"},
    "snow_mult": {"label": "Snowpack multiplier", "min": 0, "max": 3, "step": 0.1,
                  "unit": "x", "default": 1, "group": "Cold",
                  "help": "snowfall totals and standing snow depth"},
    "ice_mult": {"label": "Freezing rain multiplier", "min": 0, "max": 3, "step": 0.1,
                 "unit": "x", "default": 1, "group": "Cold",
                 "help": "hours of ice accretion, the main winter outage driver"},
    "instability_mult": {"label": "Instability (CAPE)", "min": 0, "max": 3, "step": 0.1,
                         "unit": "x", "default": 1, "group": "Convection",
                         "help": "buoyant energy available to storms"},
    "shear_mult": {"label": "Deep-layer shear", "min": 0.3, "max": 2.5, "step": 0.1,
                   "unit": "x", "default": 1, "group": "Convection",
                   "help": "wind change with height, what organizes supercells"},
    "sst_delta_c": {"label": "Sea surface temp shift", "min": -3, "max": 4, "step": 0.5,
                    "unit": "C", "default": 0, "group": "Ocean",
                    "help": "ocean heat, the fuel for tropical cyclones"},
    "storm_distance_delta_km": {"label": "Storm track shift", "min": -600, "max": 600,
                                "step": 25, "unit": "km", "default": 0, "group": "Ocean",
                                "help": "negative brings an active cyclone closer"},
}

# named scenarios, each one a real meteorological pattern rather than a random
# slider position. served from here so the UI and the API agree on them.
PRESETS = {
    "reset": {"label": "Reset to observed", "deltas": {},
              "note": "the real conditions measured right now"},
    "heat_dome": {"label": "Heat dome",
                  "deltas": {"temp_delta_c": 6, "precip_mult": 0.1, "rh_delta_pct": -18,
                             "soil_moisture_delta": -0.06},
                  "note": "a blocking ridge: extreme heat, no rain, drying soils"},
    "atmospheric_river": {"label": "Atmospheric river",
                          "deltas": {"precip_mult": 3, "rh_delta_pct": 25, "wind_mult": 1.5,
                                     "soil_moisture_delta": 0.1, "temp_delta_c": 2},
                          "note": "days of warm Pacific moisture, the west coast flood driver"},
    "santa_ana": {"label": "Santa Ana wind event",
                  "deltas": {"wind_mult": 2.2, "rh_delta_pct": -25, "temp_delta_c": 4,
                             "precip_mult": 0},
                  "note": "offshore downslope wind: hot, bone dry, and fast"},
    "flash_drought": {"label": "Flash drought",
                      "deltas": {"precip_mult": 0.05, "temp_delta_c": 3,
                                 "rh_delta_pct": -12, "soil_moisture_delta": -0.1},
                      "note": "weeks of heat and no rain, the 2012 and 2017 pattern"},
    "derecho": {"label": "Derecho setup",
                "deltas": {"instability_mult": 2.5, "shear_mult": 1.6, "wind_mult": 2.0,
                           "temp_delta_c": 3, "rh_delta_pct": 10},
                "note": "extreme instability and shear: long-track damaging wind"},
    "polar_vortex": {"label": "Polar vortex outbreak",
                     "deltas": {"temp_delta_c": -8, "snow_mult": 2.0, "ice_mult": 1.8,
                                "wind_mult": 1.6},
                     "note": "arctic air displaced south, the February 2021 pattern"},
    "hurricane_landfall": {"label": "Hurricane approach",
                           "deltas": {"sst_delta_c": 2, "storm_distance_delta_km": -400,
                                      "wind_mult": 2.2, "precip_mult": 2.8,
                                      "soil_moisture_delta": 0.12},
                           "note": "a warm ocean and a track that closes on this point"},
    "climate_2c": {"label": "+2 C world", "deltas": {"temp_delta_c": 2, "sst_delta_c": 1.2},
                   "note": "the Paris upper bound, roughly the 2050s on current trends"},
    "climate_4c": {"label": "+4 C world",
                   "deltas": {"temp_delta_c": 4, "sst_delta_c": 2.5, "precip_mult": 0.85,
                              "soil_moisture_delta": -0.05},
                   "note": "high-emissions end of century, drier soils in most of the US"},
}


def baseline(snap):
    """per-module features + scores from a full assessment pass"""
    from src.modules import runner
    envs, scores = {}, {}
    for slug, meta in MODULES.items():
        if not meta.get("simulatable"):
            continue
        r = runner.assess(slug, snap)
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
    im = 1.0 if deltas.get("ice_mult") is None else float(deltas["ice_mult"])
    cape_m = 1.0 if deltas.get("instability_mult") is None else float(deltas["instability_mult"])
    shear_m = 1.0 if deltas.get("shear_mult") is None else float(deltas["shear_mult"])
    dsoil = float(deltas.get("soil_moisture_delta") or 0)
    dsst = float(deltas.get("sst_delta_c") or 0)
    ddist = float(deltas.get("storm_distance_delta_km") or 0)

    # --- temperature ---
    for k in ["tmax_c", "tmax_7d_mean", "tmin_c", "min_windchill_c", "tmin_7d_mean"]:
        if e.get(k) is not None:
            e[k] += dt

    if e.get("rh_min_pct") is not None:
        e["rh_min_pct"] = max(3.0, min(100.0, e["rh_min_pct"] + drh))

    # --- water ---
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

    if e.get("soil_moisture") is not None:
        # the explicit knob, plus what the rainfall change implies on its own.
        # soil moisture is bounded by porosity, so clamp to a physical range.
        implied = 0.06 * (pm - 1.0) - 0.012 * dt
        e["soil_moisture"] = max(0.01, min(0.55, e["soil_moisture"] + dsoil + implied))

    # --- wind and convection ---
    for k in ["wind_max_kmh", "gust_max_kmh", "wind_gust_kmh"]:
        if e.get(k) is not None:
            e[k] *= wm
    if e.get("shear_kmh") is not None:
        e["shear_kmh"] *= shear_m

    # --- cold ---
    for k in ["snowfall_3d_cm", "snowfall_5d_cm", "snow_depth_cm", "snowfall_30d_cm"]:
        if e.get(k) is not None:
            e[k] *= sm
    if e.get("ice_hours") is not None:
        # freezing rain needs a surface at or below freezing: warm the scenario
        # enough and the ice hours simply stop existing
        warm_kill = max(0.0, 1.0 - max(0.0, dt) / 6.0)
        e["ice_hours"] = e["ice_hours"] * im * pm * warm_kill

    # --- ocean ---
    if e.get("sst_c") is not None:
        e["sst_c"] += dsst
    if e.get("storm_distance_km") is not None:
        e["storm_distance_km"] = max(0.0, e["storm_distance_km"] + ddist)
    if e.get("storm_intensity_kt") is not None:
        e["wind_mult"] = wm  # cyclone quick() scales the storm with the wind knob

    # --- derived fields recomputed from the shifted state ---
    if e.get("tmax_c") is not None and e.get("rh_min_pct") is not None:
        e["vpd_kpa"] = round(vapor_pressure_deficit(e["tmax_c"], e["rh_min_pct"]), 3)
    if e.get("dryness_ratio") is not None:
        # clamp to the envelope seen in training: beyond ~15 the model has only
        # seen fuel-limited deserts and the trees stop being meaningful
        e["dryness_ratio"] = min(e["dryness_ratio"] * (1 + 0.05 * dt) / max(pm, 0.08), 15.0)
    if e.get("et0_7d_mm") is not None:
        e["et0_7d_mm"] *= (1 + 0.05 * dt)
    if e.get("et0_30d_mm") is not None:
        e["et0_30d_mm"] *= (1 + 0.05 * dt)
    if e.get("cape") is not None:
        # Clausius-Clapeyron moisture gain with temperature, times the knob
        e["cape"] = max(0.0, e["cape"] * (1 + 0.07 * dt) * cape_m)
    if e.get("hot_days") is not None and dt:
        # a warmer scenario pushes more forecast days over the fixed p90 bar
        e["hot_days"] = max(0, min(7, round(e["hot_days"] + dt * 1.2)))

    return e


# the baseline (real conditions) is expensive to build, but it does not change
# while a user drags sliders, so cache it per location for 10 minutes. this is
# what makes the simulator feel instant: only quick() re-scoring runs per drag.
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


def _score_states(envs, deltas):
    """score every module twice, unmodified and modified, through quick() only.
    returns {slug: (before, after)} plus the modified feature dicts."""
    out, modified_all = {}, {}
    for slug, feats in envs.items():
        quick = getattr(MODULES[slug]["impl"], "quick", None)
        if not quick:
            continue
        modified = apply_deltas(feats, deltas)
        modified_all[slug] = modified
        try:
            # score both states through quick() so alert-driven floors in the
            # full assessment cannot distort the before/after comparison
            before, after = quick(feats), quick(modified)
        except Exception:
            continue
        if before is None or after is None:
            continue
        out[slug] = (float(before), float(after))
    return out, modified_all


def run(snap, deltas, couple=True):
    """baseline vs modified scores for every simulatable module"""
    envs, base_scores = cached_baseline(snap)
    if not envs:
        return {"error": "Could not build a baseline for this location."}

    states, modified_all = _score_states(envs, deltas)
    if not states:
        return {"error": "No model could be re-scored for this location."}

    before_map = {s: v[0] for s, v in states.items()}
    after_map = {s: v[1] for s, v in states.items()}

    # cascade coupling, applied to both sides so the comparison stays fair.
    # this is what makes the simulator show knock-on effects: dry out the
    # scenario and the drought rise drags wildfire up with it.
    edges = []
    if couple:
        coupled_before, _ = cascades.apply(before_map)
        coupled_after, edges = cascades.apply(after_map)
        before_map, after_map = coupled_before, coupled_after

    rows = []
    for slug in states:
        before, after = round(before_map[slug], 1), round(after_map[slug], 1)
        rows.append({"module": slug, "title": MODULES[slug]["title"],
                     "before": before, "after": after,
                     "raw_before": round(states[slug][0], 1),
                     "raw_after": round(states[slug][1], 1),
                     "change": round(after - before, 1)})
    rows.sort(key=lambda r: -abs(r["change"]))

    def _clean(d):
        return {k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in d.items() if v is not None and not isinstance(v, dict)}

    active_edges = []
    for e in edges:
        if not (e["active"] and e["boost"] > 0):
            continue
        src = MODULES.get(e["source"], {}).get("title", e["source"])
        dst = MODULES.get(e["target"], {}).get("title", e["target"])
        active_edges.append(dict(e, label=f"{src} raises {dst}: {e['mechanism']}"))
    active_edges.sort(key=lambda e: -e["boost"])
    # knob and preset definitions are served once from /scenario/knobs, not
    # repeated on every debounced slider drag
    return {"baseline_env": {s: _clean(f) for s, f in envs.items()},
            "modified_env": {s: _clean(f) for s, f in modified_all.items()},
            "results": rows, "cascades": active_edges,
            "worst": rows[0]["module"] if rows else None,
            "baseline_scores": {s: round(v, 1) for s, v in base_scores.items()}}


# ---------------------------------------------------------------- analyses

def sensitivity(snap, deltas=None):
    """turn each knob to both ends of its range on its own and record how far
    each model moves. answers "what is this location actually exposed to?",
    which a single slider drag never can.

    deliberately uncoupled: cascade boosts are nonlinear step functions, and
    letting them fire here would attribute a knob's swing to whichever hazard
    happened to cross an activation threshold rather than to the physics."""
    envs, _ = cached_baseline(snap)
    if not envs:
        return {"error": "Could not build a baseline for this location."}
    deltas = dict(deltas or {})

    center, _ = _score_states(envs, deltas)
    rows = []
    for key, knob in KNOBS.items():
        entry = {"knob": key, "label": knob["label"], "unit": knob["unit"],
                 "group": knob["group"], "modules": {}, "swing": 0.0}
        low = dict(deltas, **{key: knob["min"]})
        high = dict(deltas, **{key: knob["max"]})
        low_states, _ = _score_states(envs, low)
        high_states, _ = _score_states(envs, high)
        for slug in center:
            base_v = center[slug][1]
            lo = low_states.get(slug, (None, base_v))[1]
            hi = high_states.get(slug, (None, base_v))[1]
            entry["modules"][slug] = {"low": round(lo, 1), "high": round(hi, 1),
                                      "base": round(base_v, 1),
                                      "range": round(abs(hi - lo), 1)}
            entry["swing"] = max(entry["swing"], abs(hi - lo))
        entry["swing"] = round(entry["swing"], 1)
        entry["driven"] = max(entry["modules"], key=lambda s: entry["modules"][s]["range"],
                              default=None)
        rows.append(entry)

    rows.sort(key=lambda r: -r["swing"])
    return {"sensitivity": rows,
            "titles": {s: MODULES[s]["title"] for s in center},
            "note": ("each knob is swept to both ends of its range with every other "
                     "knob held at the current setting, so the ranking is local to "
                     "this location and this scenario")}


def sweep(snap, knob, steps=17, deltas=None, couple=True):
    """the full response curve of every model against one knob"""
    if knob not in KNOBS:
        return {"error": f"Unknown knob '{knob}'"}
    envs, _ = cached_baseline(snap)
    if not envs:
        return {"error": "Could not build a baseline for this location."}

    meta = KNOBS[knob]
    steps = max(3, min(int(steps), 41))
    span = (meta["max"] - meta["min"]) / (steps - 1)
    values = [round(meta["min"] + span * i, 4) for i in range(steps)]

    series = {}
    for v in values:
        states, _ = _score_states(envs, dict(deltas or {}, **{knob: v}))
        scores = {s: st[1] for s, st in states.items()}
        if couple:
            scores, _ = cascades.apply(scores)
        for slug, score in scores.items():
            series.setdefault(slug, []).append(round(score, 1))

    return {"knob": knob, "label": meta["label"], "unit": meta["unit"],
            "values": values,
            "series": [{"module": s, "title": MODULES[s]["title"], "data": d}
                       for s, d in sorted(series.items(), key=lambda kv: -max(kv[1]))]}


# risk-band edges the threshold solver can target
BANDS = {"Moderate": 25.0, "High": 50.0, "Extreme": 75.0}


def threshold(snap, module, knob, target="High", deltas=None):
    """solve for the knob value at which one hazard crosses a risk band.

    turns "what if" into "how much would it take", which is the question an
    emergency manager or an underwriter actually asks."""
    if knob not in KNOBS:
        return {"error": f"Unknown knob '{knob}'"}
    if module not in MODULES or not getattr(MODULES[module]["impl"], "quick", None):
        return {"error": f"Module '{module}' cannot be simulated"}
    envs, _ = cached_baseline(snap)
    feats = envs.get(module)
    if not feats:
        return {"error": "Could not build a baseline for this location."}

    goal = BANDS.get(target, float(target) if str(target).replace(".", "").isdigit() else 50.0)
    quick = MODULES[module]["impl"].quick
    meta = KNOBS[knob]
    base_deltas = dict(deltas or {})

    def score_at(v):
        try:
            return quick(apply_deltas(feats, dict(base_deltas, **{knob: v})))
        except Exception:
            return None

    lo, hi = meta["min"], meta["max"]
    s_lo, s_hi = score_at(lo), score_at(hi)
    current = score_at(meta["default"])
    if s_lo is None or s_hi is None:
        return {"error": "This model did not respond to the knob."}

    if (s_lo - goal) * (s_hi - goal) > 0:
        return {"module": module, "title": MODULES[module]["title"], "knob": knob,
                "knob_label": meta["label"], "unit": meta["unit"], "target": target,
                "target_score": goal, "current_score": round(current or 0, 1),
                "reachable": False,
                "range_scores": [round(s_lo, 1), round(s_hi, 1)],
                "message": (f"{MODULES[module]['title']} never crosses {goal:.0f} anywhere in "
                            f"the {meta['label'].lower()} range at this location "
                            f"(it spans {min(s_lo, s_hi):.0f} to {max(s_lo, s_hi):.0f}).")}

    # 40 bisection steps is far more than needed but costs microseconds, and it
    # guarantees the answer is exact to the slider's own step size
    for _ in range(40):
        mid = (lo + hi) / 2
        s_mid = score_at(mid)
        if s_mid is None:
            break
        if (s_lo - goal) * (s_mid - goal) <= 0:
            hi, s_hi = mid, s_mid
        else:
            lo, s_lo = mid, s_mid
        if abs(hi - lo) < meta["step"] / 4:
            break

    crossing = round((lo + hi) / 2, 3)
    return {"module": module, "title": MODULES[module]["title"], "knob": knob,
            "knob_label": meta["label"], "unit": meta["unit"], "target": target,
            "target_score": goal, "current_score": round(current or 0, 1),
            "reachable": True, "value": crossing,
            "message": (f"{MODULES[module]['title']} reaches {target} risk when "
                        f"{meta['label'].lower()} hits {crossing}{meta['unit']}.")}


if __name__ == "__main__":  # python -m src.analysis.scenario
    # every knob must actually move the fields it claims to. this runs on
    # synthetic features so it needs no network and no API quota.
    probe = {"tmax_c": 25.0, "tmin_c": 12.0, "rh_min_pct": 40.0, "precip_30d_mm": 50.0,
             "precip_90d_mm": 150.0, "soil_moisture": 0.25, "wind_max_kmh": 30.0,
             "gust_max_kmh": 45.0, "shear_kmh": 40.0, "cape": 1200.0,
             "snowfall_3d_cm": 10.0, "snowfall_5d_cm": 15.0, "snow_depth_cm": 40.0,
             "ice_hours": 6.0, "sst_c": 26.0, "storm_distance_km": 800.0,
             "storm_intensity_kt": 70.0, "dryness_ratio": 2.0, "et0_7d_mm": 30.0,
             "days_since_rain": 5, "hot_days": 2, "vpd_kpa": 1.2}
    expect = {
        "temp_delta_c": (5, ["tmax_c", "tmin_c", "cape", "et0_7d_mm", "hot_days",
                             "vpd_kpa", "ice_hours"]),
        "precip_mult": (0.1, ["precip_30d_mm", "precip_90d_mm", "soil_moisture",
                              "days_since_rain"]),
        "rh_delta_pct": (-25, ["rh_min_pct", "vpd_kpa"]),
        "wind_mult": (2.0, ["wind_max_kmh", "gust_max_kmh"]),
        "soil_moisture_delta": (-0.1, ["soil_moisture"]),
        "snow_mult": (2.0, ["snowfall_3d_cm", "snowfall_5d_cm", "snow_depth_cm"]),
        "ice_mult": (0, ["ice_hours"]),
        "instability_mult": (2.0, ["cape"]),
        "shear_mult": (2.0, ["shear_kmh"]),
        "sst_delta_c": (2, ["sst_c"]),
        "storm_distance_delta_km": (-600, ["storm_distance_km"]),
    }
    bad = 0
    for key in KNOBS:
        value, fields = expect[key]
        moved = apply_deltas(probe, {key: value})
        stuck = [f for f in fields if abs(moved[f] - probe[f]) < 1e-9]
        shown = ", ".join(f"{f} {probe[f]}->{round(moved[f], 2)}" for f in fields)
        print(f"{'FAIL' if stuck else 'ok  '} {key:26s} {shown}")
        if stuck:
            print(f"       unchanged: {stuck}")
            bad += 1
    print(f"\n{len(KNOBS) - bad}/{len(KNOBS)} knobs wired, {len(PRESETS)} presets")
    raise SystemExit(1 if bad else 0)
