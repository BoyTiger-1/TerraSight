# tornado intelligence: convective ingredients (CAPE, gusts, moisture) from the
# forecast model plus live NWS watches and warnings, which remain the gold standard
from src.analysis import economics
from src.modules import base
from src.services import noaa


# tornadoes need instability AND deep-layer shear together. this is the idea
# behind SPC's supercell composite: high CAPE with weak shear gives disorganized
# airmass storms, strong shear with no CAPE gives nothing, both gives supercells.
def _score(cape_max, shear_kmh, gust_max, rh_mean):
    cape_f = base.scale(cape_max, 500, 3500)
    shear_f = base.scale(shear_kmh, 30, 90)          # ~16 to 49 kt bulk shear
    supercell = cape_f * shear_f                       # the both-ingredients term
    gust_f = base.scale(gust_max, 40, 110)
    rh_f = base.scale(rh_mean if rh_mean is not None else 30, 35, 80)
    score = (cape_f * 0.28 + shear_f * 0.28 + supercell * 0.29 + gust_f * 0.08 + rh_f * 0.07) * 100
    return score, cape_f, shear_f, supercell, gust_f, rh_f


def quick(env):
    score, *_ = _score(env.get("cape", 0), env.get("shear_kmh", 0),
                       env.get("gust_max_kmh", 0), env.get("rh_min_pct", 30))
    return round(score, 1)


def assess(snap):
    hourly = (snap.hourly() or {}).get("hourly") or {}
    times = hourly.get("time", [])
    cape_series = hourly.get("cape", [])
    gust_series = hourly.get("wind_gusts_10m", [])
    rh_series = hourly.get("relative_humidity_2m", [])
    if not times or not cape_series:
        return {"error": "Convective forecast data unavailable for this location."}

    cape_max = max((v for v in cape_series if v is not None), default=0)
    peak_i = max(range(len(cape_series)),
                 key=lambda i: cape_series[i] if cape_series[i] is not None else -1)
    gust_max = max((v for v in gust_series if v is not None), default=0)
    rh_vals = [v for v in rh_series if v is not None]
    rh_mean = sum(rh_vals) / len(rh_vals) if rh_vals else None

    # deep-layer shear proxy: 500 hPa minus 850 hPa wind speed, biggest during
    # the unstable window. a real difference of vectors would be ideal, but the
    # speed difference tracks it well enough to separate sheared from calm setups
    v850 = hourly.get("wind_speed_850hPa", [])
    v500 = hourly.get("wind_speed_500hPa", [])
    shear_vals = [abs(b - a) for a, b in zip(v850, v500) if a is not None and b is not None]
    shear_kmh = max(shear_vals) if shear_vals else 0

    score, cape_f, shear_f, supercell, gust_f, rh_f = _score(cape_max, shear_kmh, gust_max, rh_mean)

    # live NWS products override the ingredients-based estimate
    alerts = snap.alerts()
    tor_warn = noaa.alerts_matching(alerts, ["tornado warning"])
    tor_watch = noaa.alerts_matching(alerts, ["tornado watch"])
    svr = noaa.alerts_matching(alerts, ["severe thunderstorm"])
    if tor_warn:
        score = 100
    elif tor_watch:
        score = max(score, 75)
    elif svr:
        score = max(score, 55)

    factors = [
        base.factor("Peak CAPE, 48h", round(cape_max), "J/kg", cape_f * 0.28,
                    "convective fuel: 1000 is unstable, 3000+ is volatile"),
        base.factor("Deep-layer wind shear", round(shear_kmh), "km/h", shear_f * 0.28,
                    "500 vs 850 hPa winds, organizes storms into rotating supercells"),
        base.factor("Supercell setup (CAPE x shear)", round(supercell * 100), "%", supercell * 0.29,
                    "tornadoes need both ingredients at once, not either alone"),
        base.factor("Peak wind gusts", round(gust_max), "km/h", gust_f * 0.08,
                    "surface gust potential"),
        base.factor("Mean low-level humidity", round(rh_mean) if rh_mean else None, "%",
                    rh_f * 0.07, "moist boundary layer feeds updrafts"),
    ]
    if tor_warn:
        factors.insert(0, base.factor("TORNADO WARNING", "ACTIVE", "", 1.0,
                                      tor_warn[0].get("headline") or "take shelter now"))
    elif tor_watch:
        factors.insert(0, base.factor("Tornado watch", "active", "", 0.5,
                                      tor_watch[0].get("headline") or ""))
    elif svr:
        factors.insert(0, base.factor("Severe thunderstorm alert", svr[0].get("event"), "", 0.3,
                                      svr[0].get("headline") or ""))

    step = 2
    timeline = {"labels": times[::step], "series": [
        {"name": "CAPE", "data": [round(v) if v is not None else None for v in cape_series[::step]], "unit": "J/kg"},
        {"name": "Wind gusts", "data": [round(v) if v is not None else None for v in gust_series[::step]], "unit": "km/h"}]}

    if tor_warn:
        headline = "TORNADO WARNING ACTIVE. Take shelter immediately."
    elif tor_watch:
        headline = f"Tornado watch in effect. Peak CAPE {cape_max:.0f} J/kg in the next 48 hours."
    else:
        both = supercell > 0.25
        headline = (f"Supercell setup: CAPE {cape_max:.0f} J/kg with {shear_kmh:.0f} km/h deep-layer shear"
                    if both else
                    (f"Unstable but weakly sheared: CAPE {cape_max:.0f} J/kg, shear only {shear_kmh:.0f} km/h"
                     if cape_max > 1000 else
                     f"Stable atmosphere: CAPE {cape_max:.0f} J/kg, shear {shear_kmh:.0f} km/h"))
        headline += (f", peaking {times[peak_i][11:16]} on {times[peak_i][:10]}." if cape_max > 500 else ".")

    return base.result(
        "tornado", snap, score, headline=headline,
        confidence=0.9 if (tor_warn or tor_watch) else 0.65,
        factors=factors,
        features={"cape": cape_max, "shear_kmh": shear_kmh, "gust_max_kmh": gust_max, "rh_min_pct": rh_mean},
        timeline=timeline,
        map_layers={"gibs": ["clouds"]},
        recommendations=_recommendations(score, bool(tor_warn), bool(tor_watch)),
        impact=economics.estimate("tornado", snap.lat, snap.lon, score, radius_km=30),
        sources=["Open-Meteo forecast (CAPE, pressure-level winds, gusts)", "NOAA NWS alerts"],
        methodology=("Ingredients-based convective assessment combining model CAPE with a "
                     "deep-layer wind shear proxy (500 minus 850 hPa winds), following the "
                     "logic of SPC's supercell composite: tornadoes require instability and "
                     "shear together. Overridden by live NWS tornado watches and warnings. "
                     "Tornadogenesis cannot be predicted point-wise; this quantifies "
                     "environment favorability."))


def _recommendations(score, warning, watch):
    recs = []
    if warning:
        recs.append(base.rec("immediate", "everyone",
                             "Go to a basement or interior room on the lowest floor, away from windows, now",
                             "a warning means a tornado is on the ground or radar-indicated"))
    elif watch:
        recs += [
            base.rec("immediate", "residents",
                     "Identify your shelter spot and keep phones charged with alerts loud",
                     "watches mean tornadoes are possible within hours"),
            base.rec("high", "schools and venues",
                     "Review shelter-in-place routes with staff before storms arrive",
                     "large-occupancy buildings need minutes of lead time a warning does not give"),
        ]
    elif score >= 50:
        recs += [
            base.rec("high", "residents",
                     "Stay weather-aware today, know where you would shelter",
                     "the atmosphere supports rotating storms if one initiates"),
            base.rec("advisory", "outdoor event organizers",
                     "Assign someone to watch radar and define a cancellation trigger",
                     "convective days turn quickly"),
        ]
    else:
        recs.append(base.rec("advisory", "residents",
                             "No convective threat in the current window",
                             "instability stays below severe thresholds"))
    return recs
