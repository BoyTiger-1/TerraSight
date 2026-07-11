# tornado intelligence: convective ingredients (CAPE, gusts, moisture) from the
# forecast model plus live NWS watches and warnings, which remain the gold standard
from src.analysis import economics
from src.modules import base
from src.services import noaa


def quick(env):
    cape_f = base.scale(env.get("cape", 0), 500, 3500)
    gust_f = base.scale(env.get("gust_max_kmh", 0), 40, 110)
    rh_f = base.scale(env.get("rh_min_pct", 30), 35, 75)
    return round((cape_f * 0.5 + gust_f * 0.3 + rh_f * 0.2) * 100, 1)


def assess(snap):
    hourly = (snap.hourly() or {}).get("hourly") or {}
    times = hourly.get("time", [])
    cape_series = hourly.get("cape", [])
    gust_series = hourly.get("wind_gusts_10m", [])
    rh_series = hourly.get("relative_humidity_2m", [])
    if not times or not cape_series:
        return {"error": "Convective forecast data unavailable for this location."}

    cape_max = max((v for v in cape_series if v is not None), default=0)
    # find when instability peaks so the headline can say "this evening"
    peak_i = max(range(len(cape_series)),
                 key=lambda i: cape_series[i] if cape_series[i] is not None else -1)
    gust_max = max((v for v in gust_series if v is not None), default=0)
    rh_mean = None
    rh_vals = [v for v in rh_series if v is not None]
    if rh_vals:
        rh_mean = sum(rh_vals) / len(rh_vals)

    cape_f = base.scale(cape_max, 500, 3500)
    gust_f = base.scale(gust_max, 40, 110)
    rh_f = base.scale(rh_mean or 30, 35, 75)
    score = (cape_f * 0.5 + gust_f * 0.3 + rh_f * 0.2) * 100

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
        base.factor("Peak CAPE, 48h", round(cape_max), "J/kg", cape_f * 0.5,
                    "convective fuel: 1000 is unstable, 3000+ is volatile"),
        base.factor("Peak wind gusts", round(gust_max), "km/h", gust_f * 0.3,
                    "strong gusts suggest organized storm dynamics"),
        base.factor("Mean low-level humidity", round(rh_mean) if rh_mean else None, "%",
                    rh_f * 0.2, "moist boundary layer feeds updrafts"),
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
        level = "volatile" if cape_max > 2500 else "unstable" if cape_max > 1000 else "stable"
        headline = (f"Atmosphere is {level}: peak CAPE {cape_max:.0f} J/kg"
                    + (f" around {times[peak_i][11:16]} on {times[peak_i][:10]}" if cape_max > 500 else "")
                    + f", gusts to {gust_max:.0f} km/h.")

    return base.result(
        "tornado", snap, score, headline=headline,
        confidence=0.9 if (tor_warn or tor_watch) else 0.65,
        factors=factors,
        features={"cape": cape_max, "gust_max_kmh": gust_max, "rh_min_pct": rh_mean},
        timeline=timeline,
        map_layers={"gibs": ["clouds"]},
        recommendations=_recommendations(score, bool(tor_warn), bool(tor_watch)),
        impact=economics.estimate("tornado", snap.lat, snap.lon, score, radius_km=30),
        sources=["Open-Meteo forecast (CAPE, gusts)", "NOAA NWS alerts"],
        methodology=("Ingredients-based convective assessment from model CAPE, gusts, "
                     "and boundary-layer moisture, overridden by live NWS tornado "
                     "watches and warnings. Tornadogenesis cannot be predicted point-wise; "
                     "this module quantifies environment favorability, exactly as SPC "
                     "outlooks do."))


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
