# landslide intelligence: slope stability under rainfall loading, using the
# Caine (1980) intensity-duration threshold that NASA's global landslide
# nowcast is also built around, plus burn-scar and seismic destabilizers
from src.analysis import economics
from src.ml import features as F
from src.modules import base


def caine_threshold(duration_h):
    """rainfall intensity (mm/h) above which shallow slides initiate.
    I = 14.82 * D^-0.39, the classic global threshold curve."""
    return 14.82 * (duration_h ** -0.39)


def quick(env):
    slope = env.get("slope_deg", 5)
    terrain_f = base.scale(slope, 8, 35)
    rain24 = env.get("precip_1d_mm", 0)
    intensity = rain24 / 24.0
    exceed = base.scale(intensity / max(caine_threshold(24), 0.1), 0.3, 1.5)
    wet = base.scale(env.get("precip_30d_mm", 0), 60, 350)
    return round(min(terrain_f * (exceed * 0.6 + wet * 0.4) * 130, 100), 1)


def assess(snap):
    frame = snap.daily()
    idx = snap.today_index()
    terr = snap.terrain()
    if not frame or idx is None:
        return {"error": "Weather data unavailable for this location."}
    slope = (terr or {}).get("slope", 0)
    relief = (terr or {}).get("relief", 0)

    feats = F.flood_features(frame, idx)

    # worst rainfall day across the forecast window drives the trigger check
    worst_day, worst_rain = frame["time"][idx], frame["precip"][idx] or 0
    for i in range(idx, min(idx + 7, len(frame["time"]))):
        if (frame["precip"][i] or 0) > worst_rain:
            worst_rain, worst_day = frame["precip"][i] or 0, frame["time"][i]

    intensity = worst_rain / 24.0
    threshold = caine_threshold(24)
    exceed = base.scale(intensity / threshold, 0.3, 1.5)
    wet = base.scale(feats["precip_30d_mm"], 60, 350)
    terrain_f = base.scale(slope, 8, 35)

    # a recent quake or a fresh burn scar upstream both prime slopes to fail
    quakes = [q for q in snap.quakes(radius_km=100, days=90, min_mag=4.5)]
    fires = snap.fires_nearby()["fires"]
    burn_near = any(f.get("lat") and snap.distance_to(f["lat"], f["lon"]) < 40 for f in fires)

    score = min(terrain_f * (exceed * 0.6 + wet * 0.4) * 130, 100)
    if quakes:
        score = min(score * 1.2 + 5, 100)
    if burn_near:
        score = min(score * 1.25 + 5, 100)

    factors = [
        base.factor("Terrain slope", slope, "deg", terrain_f * 0.4,
                    "estimated from a 500 m elevation cross, slides need slopes over ~15"),
        base.factor("Peak 24h rainfall (7-day window)", round(worst_rain, 1), "mm",
                    exceed * 0.35, f"Caine threshold at 24h is {threshold * 24:.0f} mm"),
        base.factor("30-day antecedent rain", feats["precip_30d_mm"], "mm",
                    wet * 0.25, "saturated slopes fail at lower trigger intensities"),
        base.factor("Local relief", relief, "m", base.scale(relief, 20, 300) * 0.1,
                    "height variation across the sampled cross"),
    ]
    if quakes:
        factors.insert(0, base.factor("Recent M4.5+ earthquakes", len(quakes), "in 90 days",
                                      0.2, "shaking opens cracks that rain later exploits"))
    if burn_near:
        factors.insert(0, base.factor("Recent fire activity nearby", "yes", "", 0.2,
                                      "burn scars shed water and debris for 2-5 years"))

    days = frame["time"][idx:idx + 7]
    timeline = {"labels": days, "series": [
        {"name": "Forecast rain", "data": [round(v or 0, 1) for v in frame["precip"][idx:idx + 7]], "unit": "mm"},
        {"name": "24h trigger threshold", "data": [round(threshold * 24, 1)] * len(days), "unit": "mm"}]}

    stability = "unstable" if score >= 60 else "marginal" if score >= 35 else "stable"
    headline = (f"Slopes here look {stability}: {slope:.0f} degree terrain, "
                f"{feats['precip_30d_mm']:.0f} mm of rain in 30 days, "
                f"peak day ahead {worst_rain:.0f} mm on {worst_day}.")

    return base.result(
        "landslide", snap, score, headline=headline,
        confidence=0.75 if terr else 0.45,
        factors=factors,
        features={"slope_deg": slope, "precip_1d_mm": worst_rain,
                  "precip_30d_mm": feats["precip_30d_mm"]},
        timeline=timeline,
        map_layers={"gibs": ["precip"],
                    "points": [{"lat": q["lat"], "lon": q["lon"], "kind": "quake",
                                "label": f"M{q['magnitude']} {q['place']}"} for q in quakes[:20]]},
        recommendations=_recommendations(score, burn_near),
        impact=economics.estimate("landslide", snap.lat, snap.lon, score, radius_km=25),
        sources=["Open-Meteo forecast + elevation", "USGS earthquake catalog",
                 "NASA fire detections"],
        methodology=("Rainfall intensity-duration compared against the Caine (1980) "
                     "global landslide initiation threshold, weighted by slope from a "
                     "500 m elevation cross and 30-day antecedent moisture. Recent "
                     "earthquakes and burn scars raise susceptibility, following the "
                     "logic of NASA's LHASA global nowcast."))


def _recommendations(score, burn_near):
    recs = []
    if score >= 60:
        recs += [
            base.rec("immediate", "residents",
                     "Watch for cracked ground, leaning poles, or muddy springs on slopes above you, and be ready to leave",
                     "these are the observable precursors of slope failure"),
            base.rec("immediate", "emergency managers",
                     "Flag canyon-mouth and toe-of-slope neighborhoods for possible evacuation",
                     "debris flows follow drainages and give minutes of warning at best"),
            base.rec("high", "road agencies",
                     "Pre-position closure barriers on cut-slope corridors",
                     "most landslide fatalities in the US happen on roads"),
        ]
    elif score >= 35:
        recs.append(base.rec("high", "residents",
                             "Keep gutters, culverts, and hillside drains clear before the next storm",
                             "concentrated runoff is what pushes marginal slopes over"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "No slope concerns in the current window",
                             "rainfall stays well under initiation thresholds"))
    if burn_near:
        recs.append(base.rec("high", "residents below burn scars",
                             "Treat every heavy-rain forecast as a debris flow risk for the next 2-5 years",
                             "post-fire soils repel water and shed debris at a fraction of normal trigger rainfall"))
    return recs
