# avalanche intelligence: the classic danger drivers, new snow load, wind slab,
# rapid warming, and rain-on-snow, evaluated on real forecast snowpack data
from src.analysis import economics
from src.ml import features as F
from src.modules import base


def quick(env):
    load = base.scale(env.get("snowfall_3d_cm", 0), 15, 60)
    wind = base.scale(env.get("wind_max_kmh", 0), 25, 70) if env.get("snowfall_3d_cm", 0) > 5 else 0
    warm = base.scale(env.get("tmax_c", -5), 0, 8)
    depth_ok = 1.0 if env.get("snow_depth_cm", 0) > 30 else base.scale(env.get("snow_depth_cm", 0), 5, 30)
    return round(min((load * 0.45 + wind * 0.3 + warm * 0.25) * depth_ok * 115, 100), 1)


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    terr = snap.terrain() or {}
    if not frame or idx is None:
        return {"error": "Weather data unavailable for this location."}

    hourly = (snap.hourly() or {}).get("hourly") or {}
    depth_series = [v for v in hourly.get("snow_depth", []) if v is not None]
    depth_cm = (depth_series[0] * 100) if depth_series else 0.0  # API returns meters

    snow_3d = sum(v or 0 for v in frame["snowfall"][max(idx - 2, 0):idx + 1])
    snow_next3 = sum(v or 0 for v in frame["snowfall"][idx:idx + 3])
    wind_max = max((v or 0) for v in frame["wind_max"][max(idx - 1, 0):idx + 2])
    tmax_next = max((v for v in frame["tmax"][idx:idx + 3] if v is not None), default=0)
    rain_on_snow = sum(v or 0 for v in frame["precip"][idx:idx + 3]) - snow_next3 * 0.7

    slope = terr.get("slope", 0)
    elev = terr.get("elevation", 0)

    if depth_cm < 5 and snow_3d < 2 and snow_next3 < 2:
        # no snowpack, no avalanche problem, be honest about it
        score = 0.0
        headline = "No meaningful snowpack at this location right now."
    else:
        load = base.scale(snow_3d + snow_next3, 15, 60)
        wind = base.scale(wind_max, 25, 70) if (snow_3d + snow_next3) > 5 else 0
        warm = base.scale(tmax_next, 0, 8)
        ros = base.scale(max(rain_on_snow, 0), 2, 20)
        depth_ok = 1.0 if depth_cm > 30 else base.scale(depth_cm, 5, 30)
        score = min((load * 0.4 + wind * 0.25 + warm * 0.2 + ros * 0.15) * depth_ok * 115, 100)
        # avalanche terrain needs 25-45 degree slopes; gentle ground caps the risk
        if slope < 15:
            score = min(score, 35)
        headline = (f"Snowpack about {depth_cm:.0f} cm. {snow_3d + snow_next3:.0f} cm of "
                    f"new snow around now, winds to {wind_max:.0f} km/h"
                    + (f", warmup to {tmax_next:.0f} C ahead." if tmax_next > 0 else "."))

    factors = [
        base.factor("New snow, 3 days back + 3 ahead", round(snow_3d + snow_next3, 1), "cm",
                    base.scale(snow_3d + snow_next3, 15, 60) * 0.4,
                    "rapid loading is the top avalanche driver"),
        base.factor("Peak wind during loading", round(wind_max), "km/h",
                    (base.scale(wind_max, 25, 70) if (snow_3d + snow_next3) > 5 else 0) * 0.25,
                    "wind builds dense slabs on lee slopes"),
        base.factor("Warmest day, next 3", round(tmax_next, 1), "C",
                    base.scale(tmax_next, 0, 8) * 0.2, "rapid warming weakens bonds"),
        base.factor("Snow depth", round(depth_cm), "cm",
                    0.1 if depth_cm > 30 else 0.0, "enough snowpack to slide"),
        base.factor("Terrain slope", slope, "deg",
                    0.1 if 25 <= slope <= 45 else -0.1,
                    "slab avalanches release on 25-45 degree slopes"),
    ]
    if rain_on_snow > 2:
        factors.insert(0, base.factor("Rain on snow", round(rain_on_snow, 1), "mm", 0.2,
                                      "rain destroys snow strength within hours"))

    days = frame["time"][idx:idx + 7]
    timeline = {"labels": days, "series": [
        {"name": "Forecast snowfall", "data": [round(v or 0, 1) for v in frame["snowfall"][idx:idx + 7]], "unit": "cm"},
        {"name": "Max temperature", "data": [round(v, 1) if v is not None else None for v in frame["tmax"][idx:idx + 7]], "unit": "C"}]}

    return base.result(
        "avalanche", snap, score, headline=headline,
        confidence=0.7 if depth_series else 0.5,
        factors=factors,
        features={"snow_depth_cm": depth_cm, "snowfall_3d_cm": snow_3d + snow_next3,
                  "wind_max_kmh": wind_max, "tmax_c": tmax_next},
        timeline=timeline,
        map_layers={"gibs": ["snow"]},
        recommendations=_recommendations(score, elev),
        impact=economics.estimate("avalanche", snap.lat, snap.lon, score, radius_km=15),
        sources=["Open-Meteo forecast (snow depth, snowfall)", "Open-Meteo elevation"],
        methodology=("Danger blended from new snow loading, wind slab formation, "
                     "rapid warming, and rain-on-snow, gated by snowpack depth and "
                     "terrain slope. Mirrors the factors avalanche centers weight in "
                     "public danger ratings. Always defer to your regional avalanche center."))


def _recommendations(score, elev):
    recs = []
    if score >= 60:
        recs += [
            base.rec("immediate", "backcountry travelers",
                     "Stay off and out from under slopes steeper than 30 degrees",
                     "new load plus wind slab is the recipe for human-triggered slides"),
            base.rec("immediate", "road agencies",
                     "Evaluate avalanche path closures and control work",
                     "storm slabs release naturally in the 24-48 h after loading"),
            base.rec("high", "ski patrol",
                     "Full control routes before opening lee terrain",
                     "wind-loaded pockets will be reactive"),
        ]
    elif score >= 35:
        recs += [
            base.rec("high", "backcountry travelers",
                     "Carry beacon, shovel, probe, travel one at a time on steep slopes",
                     "moderate danger kills more travelers than extreme, exposure is higher"),
            base.rec("advisory", "backcountry travelers",
                     "Check your regional avalanche center bulletin before heading out",
                     "local observations beat any model"),
        ]
    else:
        recs.append(base.rec("advisory", "backcountry travelers",
                             "No significant avalanche signals in the forecast window",
                             "snowpack is shallow or stable in the current data"))
    return recs
