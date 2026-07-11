# drought intelligence: standardized precipitation deficits against a 30-year
# local baseline, plus live soil moisture and evaporative demand. this mirrors
# how the US Drought Monitor actually classifies drought, using indices.
from src.analysis import economics
from src.ml import features as F
from src.modules import base

# US Drought Monitor style categories mapped onto our 0-100 score
CATEGORIES = [(85, "D4 Exceptional drought"), (70, "D3 Extreme drought"),
              (55, "D2 Severe drought"), (40, "D1 Moderate drought"),
              (25, "D0 Abnormally dry"), (0, "No drought")]


def _seasonal_90d_totals(clim):
    """for each of the 30 baseline years, the 90-day precip total ending on
    today's calendar date. gives a same-season distribution to rank against."""
    daily = (clim or {}).get("daily") or {}
    times, pr = daily.get("time", []), daily.get("precipitation_sum", [])
    if len(times) < 400:
        return []
    end_md = times[-1][5:]  # anchor on the archive's last calendar day
    totals = []
    for i, t in enumerate(times):
        if t[5:] == end_md and i >= 90:
            totals.append(sum(v for v in pr[i - 89:i + 1] if v is not None))
    return totals


def category(score):
    for cutoff, name in CATEGORIES:
        if score >= cutoff:
            return name
    return "No drought"


def quick(env):
    """simulator path: rank the (possibly modified) 90-day rain against normal"""
    normal = env.get("precip_90d_normal_mm") or 1.0
    ratio = env.get("precip_90d_mm", normal) / normal
    deficit = base.scale(1 - ratio, 0, 0.8)                     # 0 rain = full deficit
    demand = base.scale(env.get("dryness_ratio", 1), 1.0, 6.0)  # evap demand vs supply
    soil = 1 - base.scale(env.get("soil_moisture", 0.25), 0.08, 0.35)
    return round((deficit * 0.5 + demand * 0.25 + soil * 0.25) * 100, 1)


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    if not frame or idx is None:
        return {"error": "Weather data unavailable for this location."}
    feats = F.wildfire_features(frame, idx)  # reuses the moisture-balance features
    clim_totals = _seasonal_90d_totals(snap.climatology())

    pctl = base.percentile_of(feats["precip_90d_mm"], clim_totals) if clim_totals else None
    normal = sorted(clim_totals)[len(clim_totals) // 2] if clim_totals else None

    hourly = (snap.hourly() or {}).get("hourly") or {}
    sm = [v for v in hourly.get("soil_moisture_3_to_9cm", []) if v is not None]
    soil_now = sm[0] if sm else None

    # three ingredients, weighted like quick() so the simulator stays consistent
    deficit = 1 - pctl if pctl is not None else base.scale(1 - feats["precip_90d_mm"] / max(normal or 200, 1), 0, 0.8)
    demand = base.scale(feats["dryness_ratio"], 1.0, 6.0)
    soil = 1 - base.scale(soil_now, 0.08, 0.35) if soil_now is not None else demand
    score = (deficit * 0.5 + demand * 0.25 + soil * 0.25) * 100

    cat = category(score)
    factors = [
        base.factor("90-day rainfall percentile",
                    round(pctl * 100) if pctl is not None else None, "%",
                    deficit * 0.5, "vs the same season across 30 years of ERA5"),
        base.factor("90-day rainfall", feats["precip_90d_mm"], "mm",
                    -0.2 if pctl and pctl > 0.5 else 0.2,
                    f"local normal is about {normal:.0f} mm" if normal else ""),
        base.factor("Evaporative demand ratio", feats["dryness_ratio"], "",
                    demand * 0.25, "atmospheric water demand vs rainfall supply, 30 days"),
        base.factor("Soil moisture 3-9 cm", soil_now, "m3/m3",
                    soil * 0.25, "below 0.15 most crops begin to stress"),
        base.factor("Days since wetting rain", feats["days_since_rain"], "days",
                    base.scale(feats["days_since_rain"], 5, 45) * 0.15, ""),
    ]

    # 12-month precipitation history for the timeline
    daily = (snap.climatology() or {}).get("daily") or {}
    times, pr = daily.get("time", []), daily.get("precipitation_sum", [])
    months, sums = [], {}
    for t, v in zip(times[-400:], pr[-400:]):
        key = t[:7]
        sums[key] = sums.get(key, 0) + (v or 0)
    months = sorted(sums.keys())[-13:-1]

    headline = (f"{cat}. 90-day rainfall is "
                + (f"in the {pctl*100:.0f}th percentile of the 30-year record"
                   if pctl is not None else f"{feats['precip_90d_mm']:.0f} mm")
                + f", {feats['days_since_rain']} days since wetting rain.")

    confidence = 0.85 if clim_totals and soil_now is not None else 0.6

    return base.result(
        "drought", snap, score, headline=headline, confidence=confidence,
        factors=factors, features={**feats, "soil_moisture": soil_now,
                                   "precip_90d_normal_mm": normal},
        timeline={"labels": months, "series": [
            {"name": "Monthly rainfall", "data": [round(sums[m], 1) for m in months], "unit": "mm"}]},
        map_layers={"gibs": ["soil"]},
        recommendations=_recommendations(score, cat),
        impact=economics.estimate("drought", snap.lat, snap.lon, score, radius_km=80),
        sources=["Open-Meteo ERA5 archive (30-year baseline)", "Open-Meteo forecast (soil moisture)"],
        methodology=("Standardized 90-day precipitation ranked against the same season "
                     "in 30 years of ERA5 reanalysis, weighted with live soil moisture "
                     "and evaporative demand. Categories follow US Drought Monitor bands."),
        extras={"category": cat})


def _recommendations(score, cat):
    recs = []
    if score >= 70:
        recs += [
            base.rec("immediate", "water utilities",
                     "Activate drought contingency stages and mandatory use restrictions",
                     f"conditions rank as {cat}"),
            base.rec("high", "farmers",
                     "Prioritize irrigation to highest-value fields, verify allocation forecasts",
                     "soil moisture and rainfall percentile both signal deep deficit"),
            base.rec("high", "emergency managers",
                     "Coordinate with fire agencies: drought of this depth primes wildfire fuels",
                     "cascading fire risk rises sharply beyond D2"),
        ]
    elif score >= 40:
        recs += [
            base.rec("high", "farmers",
                     "Shift to deficit irrigation scheduling using this week's ET0 numbers",
                     "meeting full crop demand wastes allocation during moderate drought"),
            base.rec("advisory", "water utilities",
                     "Begin voluntary conservation messaging",
                     "early messaging measurably cuts peak demand"),
        ]
    else:
        recs.append(base.rec("advisory", "residents",
                             "No drought stress. Reasonable window for planting and turf renovation",
                             "moisture conditions are at or above seasonal normal"))
    return recs
