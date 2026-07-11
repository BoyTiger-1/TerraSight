# flood intelligence: ML rainfall-flood model trained on 37 real US floods,
# blended with GloFAS river discharge percentiles and live USGS gauge readings
from src.analysis import economics
from src.config import risk_band
from src.ml import features as F
from src.ml.registry import get_model
from src.modules import base
from src.services import noaa

LABELS = {
    "precip_1d_mm": ("Rain today", "mm", "single-day totals drive flash floods"),
    "precip_3d_mm": ("Rain, 3 days", "mm", "back-to-back storms overwhelm drainage"),
    "precip_7d_mm": ("Rain, 7 days", "mm", "weekly accumulation fills rivers"),
    "precip_30d_mm": ("Rain, 30 days", "mm", "monthly total sets the stage"),
    "precip_max1d_30d_mm": ("Biggest day, last 30", "mm", "recent extreme days show storm intensity"),
    "api_index": ("Antecedent precipitation index", "", "decay-weighted recent rain, a soil wetness proxy"),
    "wet_days_30d": ("Wet days, last 30", "days", "frequent rain keeps soils saturated"),
    "tmax_c": ("Max temperature", "C", "warm air holds more storm moisture, melts snow"),
    "tmin_c": ("Min temperature", "C", "freezing level controls rain vs snow"),
    "snowfall_30d_cm": ("Snowfall, 30 days", "cm", "snowpack becomes runoff when warmth arrives"),
    "et0_30d_mm": ("Evaporation, 30 days", "mm", "dry-downs give soils room to absorb"),
}


def quick(env):
    model = get_model("flood")
    if not model:
        return None
    p = model.predict(env)
    # discharge percentile override carries into the simulator too
    pctl = env.get("discharge_pctl")
    score = p * 100
    if pctl is not None and pctl > 0.9:
        score = max(score, 55 + (pctl - 0.9) * 350)
    return round(min(score, 100), 1)


def _discharge_context(flood_resp):
    """current + forecast river discharge vs the recent 60-day distribution"""
    daily = (flood_resp or {}).get("daily") or {}
    q = [v for v in daily.get("river_discharge", []) if v is not None]
    if len(q) < 30:
        return None
    past, future = q[:60], q[60:]
    now = past[-1] if past else None
    peak_fc = max(future) if future else now
    pctl = base.percentile_of(peak_fc, past) if peak_fc is not None else None
    return {"time": daily.get("time", []), "series": daily.get("river_discharge", []),
            "current": now, "forecast_peak": peak_fc, "peak_percentile": pctl}


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    model = get_model("flood")
    if not frame or idx is None or not model:
        return {"error": "Weather data or trained model unavailable for this location."}

    feats = F.flood_features(frame, idx)
    if not feats:
        return {"error": "Not enough weather history at this location."}

    prob, explanation = model.explain(feats)
    score = prob * 100

    # forecast rain can outweigh today: score the wettest of the next 7 days too
    best_day = feats
    for i in range(idx + 1, min(idx + 8, len(frame["time"]))):
        f_i = F.flood_features(frame, i)
        if f_i:
            p_i = model.predict(f_i)
            if p_i * 100 > score:
                score, best_day = p_i * 100, f_i
                prob, explanation = model.explain(f_i)

    # GloFAS: if the river is forecast above its 90th percentile, floor the score
    ctx = _discharge_context(snap.flood())
    if ctx and ctx["peak_percentile"] is not None and ctx["peak_percentile"] > 0.9:
        score = max(score, 55 + (ctx["peak_percentile"] - 0.9) * 350)

    alerts = noaa.alerts_matching(snap.alerts(), ["flood", "flash flood", "hydrologic"])
    if any("warning" in (a.get("event") or "").lower() for a in alerts):
        score = max(score, 80)
    elif alerts:
        score = max(score, 55)

    factors = base.ml_factors(explanation, LABELS)
    if ctx and ctx["peak_percentile"] is not None:
        factors.insert(0, base.factor("River discharge percentile",
                                      round(ctx["peak_percentile"] * 100), "%",
                                      0.3 if ctx["peak_percentile"] > 0.9 else 0.05,
                                      "GloFAS forecast peak vs the last 60 days"))
    if alerts:
        factors.insert(0, base.factor("NWS flood alert", alerts[0].get("event"), "", 0.4,
                                      alerts[0].get("headline") or ""))

    gauges = snap.gauges()[:12]
    gauge_points = [{"lat": g["lat"], "lon": g["lon"], "kind": "gauge",
                     "label": f"{g.get('name', 'gauge')}: "
                              f"{g.get('stage_ft', '?')} ft, {g.get('discharge_cfs', '?')} cfs"}
                    for g in gauges if g.get("lat")]

    timeline = None
    if ctx:
        timeline = {"labels": ctx["time"],
                    "series": [{"name": "River discharge", "data": ctx["series"], "unit": "m3/s"}]}

    label, _ = risk_band(score)
    headline = (f"{label} flood risk. "
                + (f"{alerts[0]['event']} in effect. " if alerts else "")
                + f"{best_day['precip_7d_mm']:.0f} mm of rain in the last 7 days"
                + (f", river at the {ctx['peak_percentile']*100:.0f}th percentile of its recent range."
                   if ctx and ctx["peak_percentile"] is not None else "."))

    auc = model.card.get("cv_roc_auc_mean", 0.8)
    confidence = min(0.95, auc * (1.0 - (0.1 if not ctx else 0)))

    return base.result(
        "flood", snap, score, headline=headline, confidence=confidence,
        factors=factors[:10], features=best_day,
        timeline=timeline,
        map_layers={"points": gauge_points, "gibs": ["precip"]},
        recommendations=_recommendations(score, alerts),
        impact=economics.estimate("flood", snap.lat, snap.lon, score, radius_km=40),
        sources=["Open-Meteo forecast + ERA5", "Open-Meteo Flood API (GloFAS v4)",
                 "USGS NWIS river gauges", "NOAA NWS alerts"],
        methodology=("Gradient-boosted classifier trained on 37 documented US flood "
                     "disasters matched with ERA5 rainfall history, blended with GloFAS "
                     "river discharge percentiles and live NWS flood alerts. "
                     f"Cross-validated ROC AUC {auc}."),
        extras={"model_card": model.card, "gauges": gauges})


def _recommendations(score, alerts):
    recs = []
    if any("warning" in (a.get("event") or "").lower() for a in alerts):
        recs.append(base.rec("immediate", "residents",
                             "Move to higher ground now. Never drive through floodwater",
                             "a flood warning means flooding is happening or imminent; most deaths occur in vehicles"))
    if score >= 75:
        recs += [
            base.rec("immediate", "residents",
                     "Move vehicles and valuables above expected water levels, charge phones",
                     "rainfall totals match the setup of past damaging floods in the training record"),
            base.rec("immediate", "emergency managers",
                     "Stage swift-water teams and pre-open shelters in low-lying districts",
                     "response time drives rescue outcomes in flash flooding"),
            base.rec("high", "businesses",
                     "Deploy flood barriers and back up critical records offsite",
                     "commercial ground floors absorb most urban flood losses"),
        ]
    elif score >= 50:
        recs += [
            base.rec("high", "residents",
                     "Clear storm drains and gutters near your property today",
                     "blocked drainage turns heavy rain into street flooding"),
            base.rec("advisory", "emergency managers",
                     "Verify river gauge telemetry and alert thresholds",
                     "the discharge trend is elevated and warning lead time depends on gauges"),
        ]
    elif score >= 25:
        recs.append(base.rec("advisory", "residents",
                             "Review whether your route to work crosses low water crossings",
                             "moderate risk days are when planning costs nothing"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "Conditions are dry. A good week to check flood insurance coverage",
                             "flood damage is excluded from standard homeowners policies"))
    recs.append(base.rec("advisory", "insurers",
                         "Discharge percentile above 90 is the leading indicator for claim clusters",
                         "riverine losses lag the hydrograph peak by hours to days"))
    return recs
