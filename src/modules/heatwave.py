# heatwave intelligence: forecast temperatures ranked against the location's own
# 30-year distribution for this time of year. a 35 C day is routine in Phoenix
# and a mass-casualty event in Seattle, percentiles capture that.
from src.analysis import economics
from src.modules import base
from src.services import noaa


def _seasonal_tmax_distribution(clim, window=10):
    """all historical tmax values within +/-10 calendar days of today"""
    daily = (clim or {}).get("daily") or {}
    times, tmax = daily.get("time", []), daily.get("temperature_2m_max", [])
    if not times:
        return []
    anchor = times[-1][5:]
    am, ad = int(anchor[:2]), int(anchor[3:])
    vals = []
    for t, v in zip(times, tmax):
        if v is None:
            continue
        m, d = int(t[5:7]), int(t[8:10])
        # crude day-of-year distance that tolerates month boundaries
        doy_t, doy_a = m * 30.4 + d, am * 30.4 + ad
        dist = min(abs(doy_t - doy_a), 365 - abs(doy_t - doy_a))
        if dist <= window:
            vals.append(v)
    return vals


def _pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(int(len(s) * q), len(s) - 1)]


def quick(env):
    """simulator path: exceedance of the (possibly shifted) forecast peak over p90"""
    p90, p975 = env.get("tmax_p90"), env.get("tmax_p975")
    peak = env.get("tmax_c")
    if p90 is None or peak is None:
        return None
    exceed = base.scale(peak - p90, 0, max((p975 or p90 + 4) - p90, 2) * 2)
    streak = base.scale(env.get("hot_days", 0), 0, 5)
    return round((exceed * 0.7 + streak * 0.3) * 100, 1)


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    if not frame or idx is None:
        return {"error": "Weather data unavailable for this location."}

    dist = _seasonal_tmax_distribution(snap.climatology())
    p90, p975 = _pct(dist, 0.90), _pct(dist, 0.975)
    if p90 is None:
        return {"error": "Could not build a temperature baseline for this location."}

    fut_days = frame["time"][idx:idx + 7]
    fut_tmax = [v for v in frame["tmax"][idx:idx + 7]]
    peak = max((v for v in fut_tmax if v is not None), default=None)
    hot_days = sum(1 for v in fut_tmax if v is not None and v >= p90)
    # longest consecutive run over p90, the classic heatwave definition
    run = best_run = 0
    for v in fut_tmax:
        run = run + 1 if (v is not None and v >= p90) else 0
        best_run = max(best_run, run)

    hourly = (snap.hourly() or {}).get("hourly") or {}
    app = [v for v in hourly.get("apparent_temperature", []) if v is not None]
    feels_peak = max(app) if app else None

    exceed = base.scale(peak - p90, 0, max((p975 or p90 + 4) - p90, 2) * 2)
    streak = base.scale(hot_days, 0, 5)
    score = (exceed * 0.7 + streak * 0.3) * 100

    alerts = noaa.alerts_matching(snap.alerts(), ["heat", "excessive heat"])
    if any("excessive heat warning" in (a.get("event") or "").lower() for a in alerts):
        score = max(score, 80)
    elif alerts:
        score = max(score, 55)

    # overnight lows matter for mortality: hot nights stop the body recovering
    fut_tmin = [v for v in frame["tmin"][idx:idx + 7] if v is not None]
    warm_nights = sum(1 for v in fut_tmin if v >= 21)

    factors = [
        base.factor("Forecast peak temperature", round(peak, 1) if peak else None, "C",
                    exceed * 0.7, f"local 90th percentile for this season is {p90:.1f} C"),
        base.factor("Days at or above p90", hot_days, f"of next {len(fut_days)}",
                    streak * 0.3, "three or more consecutive days is a heatwave"),
        base.factor("Longest hot streak", best_run, "days", base.scale(best_run, 0, 5) * 0.2, ""),
        base.factor("Peak feels-like", round(feels_peak, 1) if feels_peak else None, "C",
                    base.scale(feels_peak or 0, 32, 46) * 0.2, "humidity-adjusted apparent temperature"),
        base.factor("Nights above 21 C", warm_nights, "of 7",
                    base.scale(warm_nights, 0, 5) * 0.15,
                    "warm nights drive heat mortality by preventing recovery"),
    ]
    if alerts:
        factors.insert(0, base.factor("NWS heat alert", alerts[0].get("event"), "", 0.35,
                                      alerts[0].get("headline") or ""))

    headline = (f"Peak of {peak:.0f} C expected" if peak else "Temperatures near normal")
    if hot_days:
        headline += f", {hot_days} day(s) above the local 90th percentile ({p90:.0f} C)"
    headline += ". " + (alerts[0]["event"] + " in effect." if alerts else
                        "No NWS heat products in effect.")

    return base.result(
        "heatwave", snap, score, headline=headline,
        confidence=0.9 if dist else 0.5,
        factors=factors,
        features={"tmax_c": peak, "tmax_p90": p90, "tmax_p975": p975, "hot_days": hot_days},
        timeline={"labels": fut_days, "series": [
            {"name": "Forecast max temp", "data": [round(v, 1) if v is not None else None for v in fut_tmax], "unit": "C"},
            {"name": "Local p90 baseline", "data": [round(p90, 1)] * len(fut_days), "unit": "C"}]},
        map_layers={"gibs": ["temp"]},
        recommendations=_recommendations(score, warm_nights),
        impact=economics.estimate("heatwave", snap.lat, snap.lon, score, radius_km=60),
        sources=["Open-Meteo forecast", "Open-Meteo ERA5 archive (30-year baseline)", "NOAA NWS alerts"],
        methodology=("Forecast maxima ranked against a 30-year same-season ERA5 "
                     "distribution at this exact location. Score combines percentile "
                     "exceedance with heatwave persistence, escalated by live NWS products."))


def _recommendations(score, warm_nights):
    recs = []
    if score >= 75:
        recs += [
            base.rec("immediate", "public health",
                     "Open cooling centers and extend hours through the warm nights",
                     "heat deaths concentrate among isolated seniors without air conditioning"),
            base.rec("immediate", "residents",
                     "Check on elderly neighbors twice daily, never leave anyone in a parked car",
                     "indoor temperatures keep climbing for days into a heatwave"),
            base.rec("high", "utilities",
                     "Prepare for record evening load and pre-stage transformer spares",
                     "grid failures during extreme heat multiply mortality"),
            base.rec("high", "employers",
                     "Shift outdoor work to before 11 am and enforce shade and water breaks",
                     "occupational heat illness spikes on the second and third days"),
        ]
    elif score >= 50:
        recs += [
            base.rec("high", "residents",
                     "Plan outdoor exercise before 10 am, hydrate ahead of thirst",
                     "temperatures will run well above what this area is acclimatized to"),
            base.rec("advisory", "schools and coaches",
                     "Move practices indoors or to early morning",
                     "youth athletes acclimatize slower than adults"),
        ]
    else:
        recs.append(base.rec("advisory", "residents",
                             "Temperatures near seasonal norms. No heat precautions needed",
                             "forecast stays below the local 90th percentile"))
    if warm_nights >= 3:
        recs.append(base.rec("high", "public health",
                             "Message the overnight risk specifically",
                             f"{warm_nights} nights will stay above 21 C, blocking recovery"))
    return recs
