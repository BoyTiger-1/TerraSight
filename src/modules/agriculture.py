# agriculture intelligence: crop stress from the water balance, growing degree
# days, frost exposure, and soil moisture. built on the same FAO-56 quantities
# real irrigation schedulers use.
from src.analysis import economics
from src.ml import features as F
from src.modules import base


def quick(env):
    """simulator path: recompute the stress blend from modified inputs"""
    water = base.scale(env.get("dryness_ratio", 1), 0.8, 5.0)
    soil = 1 - base.scale(env.get("soil_moisture", 0.25), 0.10, 0.35)
    heat = base.scale(env.get("tmax_c", 25), 32, 42)
    frost = 1.0 if env.get("tmin_c", 10) <= 0 else 0.0
    return round((water * 0.35 + soil * 0.3 + heat * 0.2 + frost * 0.15) * 100, 1)


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    if not frame or idx is None:
        return {"error": "Weather data unavailable for this location."}
    feats = F.wildfire_features(frame, idx)  # water-balance features are shared

    hourly = (snap.hourly() or {}).get("hourly") or {}
    sm_series = [v for v in hourly.get("soil_moisture_3_to_9cm", []) if v is not None]
    soil_now = sm_series[0] if sm_series else None

    # growing degree days accumulated over the last 90 days, base 10 C (corn standard)
    gdd = 0.0
    for tmx, tmn in zip(frame["tmax"][idx - 89:idx + 1], frame["tmin"][idx - 89:idx + 1]):
        if tmx is None or tmn is None:
            continue
        gdd += max(((min(tmx, 30) + max(tmn, 10)) / 2) - 10, 0)

    # frost risk over the next 7 days
    fut_tmin = [v for v in frame["tmin"][idx:idx + 7] if v is not None]
    frost_days = sum(1 for v in fut_tmin if v <= 0)
    fut_tmax = [v for v in frame["tmax"][idx:idx + 7] if v is not None]
    heat_days = sum(1 for v in fut_tmax if v >= 35)

    water = base.scale(feats["dryness_ratio"], 0.8, 5.0)
    soil = 1 - base.scale(soil_now, 0.10, 0.35) if soil_now is not None else water
    heat = base.scale(max(fut_tmax, default=25), 32, 42)
    frost = base.scale(frost_days, 0, 3)
    score = (water * 0.35 + soil * 0.3 + heat * 0.2 + frost * 0.15) * 100

    factors = [
        base.factor("Water balance (ET0 vs rain)", feats["dryness_ratio"], "",
                    water * 0.35, "above 2, evaporation outpaces rainfall badly"),
        base.factor("Soil moisture 3-9 cm", soil_now, "m3/m3",
                    soil * 0.3, "root-zone proxy, wilting begins near 0.10"),
        base.factor("Frost days, next 7", frost_days, "days", frost * 0.15,
                    "a single hard frost can end a fruit season"),
        base.factor("Days at or above 35 C, next 7", heat_days, "days", heat * 0.2,
                    "pollination fails during flowering heat"),
        base.factor("Growing degree days, 90d", round(gdd), "GDD base 10",
                    0.0, "crop development pace, corn needs about 1500 to maturity"),
        base.factor("Rain, next 7 days",
                    round(sum(v or 0 for v in frame["precip"][idx:idx + 7]), 1), "mm",
                    -0.15, "incoming water supply"),
    ]

    days = frame["time"][idx:idx + 7]
    timeline = {"labels": days, "series": [
        {"name": "Forecast rain", "data": [round(v or 0, 1) for v in frame["precip"][idx:idx + 7]], "unit": "mm"},
        {"name": "Reference ET0", "data": [round(v or 0, 1) for v in frame["et0"][idx:idx + 7]], "unit": "mm"}]}

    stress = "high" if score >= 60 else "moderate" if score >= 35 else "low"
    headline = (f"Crop stress is {stress}. "
                f"90-day water balance ratio {feats['dryness_ratio']:.1f}, "
                + (f"soil moisture {soil_now:.2f} m3/m3, " if soil_now is not None else "")
                + f"{frost_days} frost day(s) in the forecast.")

    return base.result(
        "agriculture", snap, score, headline=headline,
        confidence=0.8 if soil_now is not None else 0.6,
        factors=factors,
        features={**feats, "soil_moisture": soil_now, "tmin_c": min(fut_tmin, default=10),
                  "gdd_90d": round(gdd)},
        timeline=timeline,
        map_layers={"gibs": ["soil", "ndvi"]},
        recommendations=_recommendations(score, frost_days, heat_days),
        impact=economics.estimate("drought", snap.lat, snap.lon, score, radius_km=60),
        sources=["Open-Meteo forecast (FAO-56 ET0, soil moisture)", "Open-Meteo ERA5 archive"],
        methodology=("FAO-56 water balance (reference evapotranspiration vs rainfall), "
                     "modeled root-zone soil moisture, growing degree day accumulation, "
                     "and forecast frost/heat exposure blended into a crop stress index."))


def _recommendations(score, frost_days, heat_days):
    recs = []
    if frost_days:
        recs.append(base.rec("immediate", "farmers",
                             "Prepare frost protection: irrigation, wind machines, or row covers tonight",
                             f"{frost_days} freezing night(s) are in the 7-day forecast"))
    if heat_days:
        recs.append(base.rec("high", "farmers",
                             "Irrigate ahead of the heat and avoid midday foliar spraying",
                             f"{heat_days} day(s) at or above 35 C will stress flowering crops"))
    if score >= 60:
        recs += [
            base.rec("high", "farmers",
                     "Switch to nighttime irrigation and check emitters for pressure loss",
                     "evaporative losses at midday can exceed 30% of applied water"),
            base.rec("advisory", "agribusiness",
                     "Expect regional yield pressure, review forward contracts",
                     "sustained water deficit during grain fill cuts yields fastest"),
        ]
    elif score >= 35:
        recs.append(base.rec("advisory", "farmers",
                             "Schedule irrigation off this week's ET0 line rather than a fixed calendar",
                             "matching demand saves water without yield cost"))
    else:
        recs.append(base.rec("advisory", "farmers",
                             "Field conditions favorable for planting, spraying, and harvest work",
                             "no significant water, frost, or heat stress in the outlook"))
    return recs
