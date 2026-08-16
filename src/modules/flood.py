# flood intelligence: ML rainfall-flood model trained on 37 real US floods,
# floored by GloFAS river discharge and live USGS gauge readings
import math

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


# Rivers are compared against their own recent high-water mark, not against a
# rank percentile. A rank is scale-blind: it reports the same "100th percentile"
# for a river forecast 2% above its two-month high as for one forecast to run ten
# times over, and on a dry channel where every past reading is 0.00 m3/s it
# saturates the instant any water at all appears. Both cases used to floor the
# score at 90 "Extreme", which is how Santa Fe scored Extreme flood on an arroyo
# peaking at 1.4 m3/s with no rain in a week, and Sacramento scored High on a
# 29.90 -> 30.48 m3/s rise.
#
# What matters hydrologically is the *ratio* of the forecast peak to the flow the
# channel has actually been carrying, plus whether there is enough water in it to
# leave the banks at all.
DISCHARGE_TRIGGER_RATIO = 1.15   # below this the river is inside its recent range
DISCHARGE_FULL_SCALE_M3S = 10.0  # a channel smaller than this cannot flood a floodplain
DISCHARGE_FLOOR_GAIN = 40.0      # 2x the recent high -> 40, 3x -> 63, 5.5x -> capped 95
DISCHARGE_FLOOR_CAP = 95.0


def _discharge_floor(peak, high):
    """minimum flood score justified by river discharge alone.

    `high` is the 90th percentile of the last 60 days: the level the river has
    recently been running at when it was already high. Exceeding it matters in
    proportion to how far, on a log scale, so each doubling adds a fixed amount
    rather than any exceedance jumping straight to Extreme.

    The result is scaled down for small channels, so a mountain creek going from
    a trickle to a slightly larger trickle stays Low no matter how many times
    over its own baseline it runs."""
    if peak is None or peak <= 0 or high is None:
        return 0.0
    # a channel whose past 60 days really are all 0.00 keeps the 0.05 floor: a dry
    # wash carrying 1000 m3/s next week is a flood. the magnitude term below is
    # what stops the same arithmetic firing on a puddle.
    ratio = peak / max(high, 0.05)
    if ratio < DISCHARGE_TRIGGER_RATIO:
        return 0.0
    magnitude = min(1.0, peak / DISCHARGE_FULL_SCALE_M3S)
    return min(DISCHARGE_FLOOR_CAP, DISCHARGE_FLOOR_GAIN * math.log2(ratio)) * magnitude


def quick(env):
    model = get_model("flood")
    if not model:
        return None
    score = model.predict(env) * 100
    # the discharge floor carries into the simulator too, so a river already out
    # of its banks cannot be dragged down to Low by the rainfall sliders alone
    peak, high = env.get("discharge_peak"), env.get("discharge_high")
    if peak is not None:
        score = max(score, _discharge_floor(peak, high))
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
    ordered = sorted(past)
    high = ordered[int(0.9 * (len(ordered) - 1))] if ordered else None
    return {"time": daily.get("time", []), "series": daily.get("river_discharge", []),
            "current": now, "forecast_peak": peak_fc,
            "recent_high": high,
            # kept for display: honest as a description of where the peak sits in
            # the recent record, just never used to drive the score
            "peak_percentile": base.percentile_of(peak_fc, past) if peak_fc is not None else None,
            "excess_ratio": (peak_fc / max(high or 0.0, 0.05)) if peak_fc else None,
            "floor": _discharge_floor(peak_fc, high)}


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    model = get_model("flood")
    # see the note in wildfire.assess: an unloadable model is the same failure at
    # every point on earth, so it must not look like a local coverage gap
    if not model:
        return {"error": "The flood model could not be loaded on this server.",
                "model_missing": True}
    if not frame or idx is None:
        return {"error": "Weather data unavailable for this location."}

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

    # GloFAS: a river forecast to run well above its recent high floors the score
    ctx = _discharge_context(snap.flood())
    if ctx:
        score = max(score, ctx["floor"])
        # carry the river state into the feature dict the simulator re-scores, so
        # quick() applies the same floor as the assessment. the model ignores keys
        # outside its own feature list, and apply_deltas leaves these alone: a
        # river already rising does not un-rise because you imagine less rain.
        best_day = dict(best_day, discharge_peak=ctx["forecast_peak"],
                        discharge_high=ctx["recent_high"])

    alerts = noaa.alerts_matching(snap.alerts(), ["flood", "flash flood", "hydrologic"])
    if any("warning" in (a.get("event") or "").lower() for a in alerts):
        score = max(score, 80)
    elif alerts:
        score = max(score, 55)

    factors = base.ml_factors(explanation, LABELS)
    if ctx and ctx["forecast_peak"] is not None:
        factors.insert(0, base.factor("River discharge, forecast peak",
                                      round(ctx["forecast_peak"], 1), "m3/s",
                                      0.3 if ctx["floor"] >= 50 else 0.05,
                                      f"GloFAS peak vs {ctx['recent_high']:.1f} m3/s, the "
                                      f"level this channel has been running at when high "
                                      f"over the last 60 days"
                                      if ctx["recent_high"] is not None else
                                      "GloFAS forecast peak over the next 30 days"))
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
    # describe the river by how far above its own normal flow it is running. the
    # old wording quoted a rank percentile, which let the headline read "0 mm of
    # rain in the last 7 days, river at the 100th percentile" on a dry channel.
    river = "."
    if ctx and ctx["excess_ratio"] is not None:
        r = ctx["excess_ratio"]
        if r >= DISCHARGE_TRIGGER_RATIO and ctx["floor"] > 0:
            # the absolute figure has to travel with the ratio: 17x on a 1.4 m3/s
            # arroyo and 3x on a 30 m3/s river are not the same news
            river = (f", river forecast to run {r:.1f}x its recent high flow "
                     f"({ctx['forecast_peak']:.1f} m3/s).")
        else:
            river = ", river within its normal range for the season."
    headline = (f"{label} flood risk. "
                + (f"{alerts[0]['event']} in effect. " if alerts else "")
                + f"{best_day['precip_7d_mm']:.0f} mm of rain in the last 7 days"
                + river)

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
                     "disasters matched with ERA5 rainfall history. The score is floored "
                     "by GloFAS river discharge when the forecast peak runs above the "
                     "level the channel has recently carried, scaled by how far above and "
                     "by the size of the channel, and by live NWS flood alerts. "
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
                         "A river forecast well above its recent high flow is the leading "
                         "indicator for claim clusters",
                         "riverine losses lag the hydrograph peak by hours to days"))
    return recs
