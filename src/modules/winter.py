# winter storm intelligence: snowfall accumulation, freezing-rain ice risk,
# dangerous cold and wind chill, and blizzard potential from the live forecast.
# fills the cold-weather gap opposite the heatwave module.
import math

from src.analysis import economics
from src.modules import base
from src.services import noaa


def wind_chill_c(t_c, wind_kmh):
    """NWS wind chill, valid for cold and breezy conditions"""
    if t_c > 10 or wind_kmh < 4.8:
        return t_c
    v = wind_kmh ** 0.16
    return 13.12 + 0.6215 * t_c - 11.37 * v + 0.3965 * t_c * v


def quick(env):
    """simulator path, reads the shifted winter feature dict"""
    snow_f = base.scale(env.get("snowfall_5d_cm", 0), 5, 50)
    cold_f = base.scale(-env.get("min_windchill_c", 0), 8, 34)
    ice_f = base.scale(env.get("ice_hours", 0), 1, 12)
    blizzard = 1.0 if (env.get("snowfall_3d_cm", 0) > 10 and env.get("wind_max_kmh", 0) > 50) else 0.0
    return round(min((snow_f * 0.4 + cold_f * 0.25 + ice_f * 0.25 + blizzard * 0.1) * 100, 100), 1)


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    if not frame or idx is None:
        return {"error": "Weather data unavailable for this location."}

    snow_3d = sum(v or 0 for v in frame["snowfall"][idx:idx + 3])
    snow_5d = sum(v or 0 for v in frame["snowfall"][idx:idx + 5])
    tmin_5d = [v for v in frame["tmin"][idx:idx + 5] if v is not None]
    min_temp = min(tmin_5d) if tmin_5d else 0
    wind_max = max((v or 0) for v in frame["wind_max"][idx:idx + 3]) if frame["wind_max"] else 0
    min_wc = wind_chill_c(min_temp, wind_max)

    # freezing rain: liquid rain falling into a near-freezing surface layer
    hourly = (snap.hourly() or {}).get("hourly") or {}
    temps = hourly.get("temperature_2m", [])
    rain = hourly.get("rain", []) or hourly.get("precipitation", [])
    ice_hours = sum(1 for t, r in zip(temps, rain)
                    if t is not None and r and r > 0.1 and -4 <= t <= 1)

    snow_f = base.scale(snow_5d, 5, 50)
    cold_f = base.scale(-min_wc, 8, 34)
    ice_f = base.scale(ice_hours, 1, 12)
    blizzard = 1.0 if (snow_3d > 10 and wind_max > 50) else 0.0
    score = min((snow_f * 0.4 + cold_f * 0.25 + ice_f * 0.25 + blizzard * 0.1) * 100, 100)

    # live NWS winter products escalate the score
    alerts = noaa.alerts_matching(snap.alerts(),
                                  ["winter storm", "blizzard", "ice storm", "wind chill",
                                   "winter weather", "cold", "snow"])
    warn = [a for a in alerts if "warning" in (a.get("event") or "").lower()]
    if any("blizzard" in (a.get("event") or "").lower() for a in warn):
        score = max(score, 85)
    elif warn:
        score = max(score, 70)
    elif alerts:
        score = max(score, 45)

    factors = [
        base.factor("Snowfall, next 5 days", round(snow_5d, 1), "cm", snow_f * 0.4,
                    "15 cm disrupts travel, 30 cm plus is a major storm"),
        base.factor("Lowest wind chill", round(min_wc), "C", cold_f * 0.25,
                    "frostbite in under 30 min below about -28 C wind chill"),
        base.factor("Freezing-rain hours", ice_hours, "of 72", ice_f * 0.25,
                    "ice accretion downs powerlines and trees, worst winter hazard for outages"),
        base.factor("Peak wind during snow", round(wind_max), "km/h", blizzard * 0.1,
                    "over 56 km/h with falling snow meets blizzard criteria"),
        base.factor("Coldest air temperature", round(min_temp, 1), "C", 0.0,
                    "before wind chill"),
    ]
    if alerts:
        factors.insert(0, base.factor("NWS winter alert", alerts[0].get("event"), "", 0.4,
                                      alerts[0].get("headline") or ""))

    days = frame["time"][idx:idx + 7]
    timeline = {"labels": days, "series": [
        {"name": "Forecast snowfall", "data": [round(v or 0, 1) for v in frame["snowfall"][idx:idx + 7]], "unit": "cm"},
        {"name": "Min temperature", "data": [round(v, 1) if v is not None else None for v in frame["tmin"][idx:idx + 7]], "unit": "C"}]}

    if score < 5 and snow_5d < 1 and min_temp > 2:
        headline = "No winter weather in the forecast. Conditions are mild."
    else:
        parts = []
        if snow_5d >= 2:
            parts.append(f"{snow_5d:.0f} cm of snow over 5 days")
        if min_wc <= -15:
            parts.append(f"wind chill down to {min_wc:.0f} C")
        if ice_hours:
            parts.append(f"{ice_hours}h of freezing rain possible")
        headline = (", ".join(parts).capitalize() if parts else "Cold conditions") + "."
        if warn:
            headline = warn[0]["event"] + " in effect. " + headline

    return base.result(
        "winter", snap, score,
        headline=headline, confidence=0.85 if hourly else 0.65,
        factors=factors,
        features={"snowfall_5d_cm": snow_5d, "snowfall_3d_cm": snow_3d,
                  "min_windchill_c": min_wc, "ice_hours": ice_hours, "wind_max_kmh": wind_max},
        timeline=timeline,
        map_layers={"gibs": ["snow"]},
        recommendations=_recommendations(score, ice_hours, min_wc, bool(warn)),
        impact=economics.estimate("default", snap.lat, snap.lon, score, radius_km=60),
        sources=["Open-Meteo forecast (snowfall, temperature, wind)", "NOAA NWS alerts"],
        methodology=("Blends forecast snowfall accumulation, NWS wind chill, freezing-rain "
                     "hours (liquid rain into a sub-freezing layer), and blizzard criteria "
                     "(snow plus 56 km/h winds), escalated by live NWS winter products."))


def _recommendations(score, ice_hours, min_wc, warning):
    recs = []
    if warning:
        recs.append(base.rec("immediate", "residents",
                             "Avoid travel during the storm; keep a blanket, water, and a charged phone if you must drive",
                             "most winter-storm deaths happen in vehicles and from overexertion"))
    if ice_hours >= 3:
        recs += [
            base.rec("immediate", "utilities",
                     "Pre-stage line crews: ice accretion is the top cause of winter outages",
                     "freezing rain loads powerlines and snaps limbs onto them"),
            base.rec("high", "residents",
                     "Charge devices and prepare for multi-day outages, never run a generator indoors",
                     "carbon monoxide from generators kills after ice storms"),
        ]
    if min_wc <= -25:
        recs.append(base.rec("immediate", "everyone",
                             "Cover all skin outdoors; frostbite sets in within 30 minutes at this wind chill",
                             f"wind chill reaches {min_wc:.0f} C"))
    if score >= 60:
        recs.append(base.rec("high", "emergency managers",
                             "Open warming centers and check on unsheltered and elderly residents",
                             "cold exposure and unheated homes drive winter mortality"))
    elif score >= 30:
        recs.append(base.rec("advisory", "residents",
                             "Stock a few days of food, water, and medications before the system arrives",
                             "moderate winter storms still close roads and schools"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "No significant winter hazard right now",
                             "snowfall, cold, and ice all stay below impactful levels"))
    return recs
