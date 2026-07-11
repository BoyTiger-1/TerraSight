# wildfire intelligence: ML ignition-weather model trained on 80 real US fires,
# adjusted for terrain and cross-checked against live detections and red flag warnings
from src.analysis import economics
from src.config import risk_band
from src.ml import features as F
from src.ml.registry import get_model
from src.modules import base
from src.services import noaa

# pretty names for the explainability panel
LABELS = {
    "tmax_c": ("Max temperature", "C", "hotter air dries fuels faster"),
    "rh_min_pct": ("Min relative humidity", "%", "below 20% fine fuels ignite readily"),
    "wind_max_kmh": ("Max wind speed", "km/h", "wind drives spread and spotting"),
    "gust_max_kmh": ("Max wind gusts", "km/h", "gusts throw embers ahead of the front"),
    "precip_7d_mm": ("Rain, last 7 days", "mm", "recent rain wets fine fuels"),
    "precip_30d_mm": ("Rain, last 30 days", "mm", "monthly moisture in medium fuels"),
    "precip_90d_mm": ("Rain, last 90 days", "mm", "seasonal moisture in heavy fuels"),
    "days_since_rain": ("Days since wetting rain", "days", "time since 2.5mm or more fell"),
    "et0_7d_mm": ("Evaporative demand, 7d", "mm", "how hard the atmosphere is drying the landscape"),
    "vpd_kpa": ("Vapor pressure deficit", "kPa", "the pull drawing moisture out of vegetation"),
    "dryness_ratio": ("Fuel dryness ratio", "", "30-day evaporation demand vs rainfall supply"),
    "tmax_7d_mean": ("Mean max temp, 7d", "C", "sustained heat cures fuels"),
}


def quick(env):
    """score from a bare feature dict, used by the scenario simulator"""
    model = get_model("wildfire")
    if not model:
        return None
    p = model.predict(env)
    return round(p * 100, 1)


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    model = get_model("wildfire")
    if not frame or idx is None or not model:
        return {"error": "Weather data or trained model unavailable for this location."}

    feats = F.wildfire_features(frame, idx)
    if not feats:
        return {"error": "Not enough weather history at this location."}

    prob, explanation = model.explain(feats)
    score = prob * 100

    # terrain steepens spread: +1% risk per degree of slope past 10, capped
    terr = snap.terrain() or {}
    slope = terr.get("slope", 0)
    slope_boost = min(max(slope - 10, 0) * 1.0, 15)
    score = min(100, score + slope_boost)

    # live escalators: an actual red flag warning or a fire already burning nearby
    alerts = noaa.alerts_matching(snap.alerts(), ["red flag", "fire weather", "fire warning"])
    fires = snap.fires_nearby()
    nearest_fire_km = None
    for fdet in fires["fires"]:
        if fdet.get("lat") is None:
            continue
        d = snap.distance_to(fdet["lat"], fdet["lon"])
        if nearest_fire_km is None or d < nearest_fire_km:
            nearest_fire_km = d
    if alerts:
        score = max(score, 65)
    if nearest_fire_km is not None and nearest_fire_km < 50:
        score = max(score, 75)

    factors = base.ml_factors(explanation, LABELS)
    if slope_boost > 0:
        factors.insert(0, base.factor("Terrain slope", slope, "deg", slope_boost / 100,
                                      "steep terrain accelerates uphill fire runs"))
    if alerts:
        factors.insert(0, base.factor("NWS Red Flag Warning", "active", "", 0.3,
                                      alerts[0].get("headline") or "critical fire weather in effect"))
    if nearest_fire_km is not None:
        factors.insert(0, base.factor("Nearest active fire", round(nearest_fire_km), "km",
                                      0.35 if nearest_fire_km < 50 else 0.05,
                                      f"detected by {fires['source']}"))

    # 7-day outlook: rerun the model on each forecast day
    days, series = [], []
    for i in range(idx, min(idx + 7, len(frame["time"]))):
        f_i = F.wildfire_features(frame, i)
        if f_i:
            days.append(frame["time"][i])
            series.append(round(model.predict(f_i) * 100, 1))

    label, _ = risk_band(score)
    headline = (f"{label} wildfire risk. "
                + (f"Active fire {nearest_fire_km:.0f} km away. " if nearest_fire_km is not None and nearest_fire_km < 100 else "")
                + (f"Red Flag Warning in effect. " if alerts else "")
                + f"{feats['days_since_rain']} days since wetting rain, "
                  f"minimum humidity {feats['rh_min_pct']:.0f}%.")

    # confidence: model quality (CV AUC) discounted if live feeds were missing
    auc = model.card.get("cv_roc_auc_mean", 0.8)
    completeness = 1.0 - (0.1 if not terr else 0) - (0.05 if fires["fires"] == [] and fires["source"] == "NASA EONET" else 0)
    confidence = min(0.95, auc * completeness)

    recs = _recommendations(score, alerts, nearest_fire_km)
    fire_points = [{"lat": f["lat"], "lon": f["lon"],
                    "label": f.get("title") or f"FRP {f.get('frp', '?')} MW",
                    "kind": "fire"} for f in fires["fires"] if f.get("lat") is not None][:60]

    return base.result(
        "wildfire", snap, score,
        headline=headline, confidence=confidence,
        factors=factors[:10], features=feats,
        timeline={"labels": days, "series": [{"name": "Fire weather risk", "data": series, "unit": "%"}]},
        map_layers={"points": fire_points, "gibs": ["thermal"]},
        recommendations=recs,
        impact=economics.estimate("wildfire", snap.lat, snap.lon, score),
        sources=["Open-Meteo forecast + ERA5", fires["source"], "NOAA NWS alerts",
                 "Open-Meteo elevation (terrain)"],
        methodology=("Gradient-boosted classifier trained on 80 documented US wildfire "
                     "ignition days (CAL FIRE, InciWeb, NIFC records) matched with ERA5 "
                     f"reanalysis weather. Cross-validated ROC AUC {auc}. Terrain slope "
                     "and live fire detections adjust the final score."),
        extras={"model_card": model.card})


def _recommendations(score, alerts, nearest_fire_km):
    recs = []
    if nearest_fire_km is not None and nearest_fire_km < 50:
        recs.append(base.rec("immediate", "residents",
                             "Check local evacuation orders now and prepare a go-bag",
                             f"an active fire is burning about {nearest_fire_km:.0f} km away"))
    if alerts:
        recs.append(base.rec("immediate", "residents",
                             "Avoid all outdoor burning, equipment sparks, and vehicle idling on dry grass",
                             "the NWS has declared critical fire weather for this area"))
    if score >= 75:
        recs += [
            base.rec("immediate", "emergency managers",
                     "Pre-position engines and air resources, staff up dispatch",
                     "conditions match the weather signature of major historical fires"),
            base.rec("high", "utilities",
                     "Evaluate public safety power shutoffs on high-wind circuits",
                     "wind-driven ignitions from powerlines caused several of the deadliest US fires"),
            base.rec("high", "residents",
                     "Clear leaves and flammables 10 m from structures, close vents and windows",
                     "ember ignition of homes is the main loss driver in wildland-urban fires"),
        ]
    elif score >= 50:
        recs += [
            base.rec("high", "residents",
                     "Delay mowing, welding, and campfires until humidity recovers",
                     "most fire starts on high-risk days are human equipment"),
            base.rec("advisory", "emergency managers",
                     "Brief crews on elevated fire weather over the next 72 hours",
                     "risk is trending in the range where initial attack speed matters"),
        ]
    elif score >= 25:
        recs.append(base.rec("advisory", "residents",
                             "Good window for defensible-space work: clear brush and gutters",
                             "moderate conditions are the safest time to reduce fuels"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "No unusual precautions needed. Review your family evacuation plan",
                             "fuels are moist and fire weather is quiet"))
    recs.append(base.rec("advisory", "insurers",
                         "Portfolio exposure in this area should reference the 7-day outlook chart",
                         "short-term ignition risk concentrates claims in wind events"))
    return recs
