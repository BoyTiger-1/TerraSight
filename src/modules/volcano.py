# volcanic activity: live USGS alert levels for every monitored US volcano,
# seismic unrest near the closest one, and downstream ashfall air quality.
# framed as impact modeling because eruptions cannot be forecast precisely.
from src.analysis import economics
from src.modules import base

# USGS aviation color codes ranked by concern
CODE_WEIGHT = {"GREEN": 0.1, "YELLOW": 0.45, "ORANGE": 0.75, "RED": 1.0}
LEVEL_WEIGHT = {"NORMAL": 0.1, "ADVISORY": 0.45, "WATCH": 0.75, "WARNING": 1.0}


def assess(snap):
    volcanoes = snap.volcanoes()
    eonet_volc = snap.eonet("volcanoes")

    # closest monitored volcano and anything elevated within 500 km
    nearest, elevated = None, []
    for v in volcanoes:
        if v.get("lat") is None:
            continue
        d = snap.distance_to(v["lat"], v["lon"])
        v2 = {**v, "distance_km": round(d)}
        if nearest is None or d < nearest["distance_km"]:
            nearest = v2
        if d < 500 and LEVEL_WEIGHT.get((v.get("alert_level") or "").upper(), 0) > 0.2:
            elevated.append(v2)

    # global eruptions from EONET in case the user is outside the US network
    erupting_near = [e for e in eonet_volc
                     if e.get("lat") is not None and
                     snap.distance_to(e["lat"], e["lon"]) < 800]

    if nearest is None and not erupting_near:
        return {"error": "No volcano monitoring data available right now."}

    score = 0.0
    factors = []
    if nearest:
        w = LEVEL_WEIGHT.get((nearest.get("alert_level") or "").upper(), 0.1)
        dist_f = 1 - base.scale(nearest["distance_km"], 30, 500)
        score = w * dist_f * 100
        factors += [
            base.factor("Nearest monitored volcano", nearest["name"], "",
                        0.0, f"{nearest['distance_km']} km away, "
                             f"observatory {nearest.get('observatory', '?')}"),
            base.factor("USGS alert level", nearest.get("alert_level"), "",
                        w * 0.5, "NORMAL, ADVISORY, WATCH, or WARNING"),
            base.factor("Aviation color code", nearest.get("color_code"), "",
                        CODE_WEIGHT.get((nearest.get("color_code") or "").upper(), 0) * 0.3,
                        "ash hazard to aircraft"),
            base.factor("Distance", nearest["distance_km"], "km", dist_f * 0.2,
                        "ashfall reaches hundreds of km, flows stay within tens"),
        ]
    for v in elevated[:3]:
        if nearest and v["name"] == nearest["name"]:
            continue
        factors.append(base.factor(f"Elevated: {v['name']}", v.get("alert_level"), "",
                                   0.15, f"{v['distance_km']} km away"))
        score = max(score, LEVEL_WEIGHT.get((v.get("alert_level") or "").upper(), 0) * 60)
    if erupting_near:
        e = erupting_near[0]
        d = snap.distance_to(e["lat"], e["lon"])
        score = max(score, (1 - base.scale(d, 50, 800)) * 85)
        factors.insert(0, base.factor("Active eruption (EONET)", e["title"], "",
                                      0.4, f"{d:.0f} km away"))

    # unrest signal: quakes clustering under the nearest volcano
    unrest_quakes = []
    if nearest and nearest["distance_km"] < 300:
        from src.services import usgs
        unrest_quakes = usgs.earthquakes(nearest["lat"], nearest["lon"],
                                         radius_km=30, days=30, min_magnitude=1.0)
        if len(unrest_quakes) > 20:
            score = min(score + 10, 100)
            factors.append(base.factor("Seismic unrest at volcano", len(unrest_quakes),
                                       "quakes/30d within 30 km", 0.2,
                                       "swarms often precede activity changes"))

    volc_points = [{"lat": v["lat"], "lon": v["lon"], "kind": "volcano",
                    "label": f"{v['name']}: {v.get('alert_level', '?')}"}
                   for v in volcanoes if v.get("lat") is not None
                   and snap.distance_to(v["lat"], v["lon"]) < 1200][:40]

    timeline = None
    if unrest_quakes:
        mags = [q["magnitude"] for q in unrest_quakes if q.get("magnitude")][:25]
        timeline = {"labels": [f"event {i+1}" for i in range(len(mags))][::-1],
                    "series": [{"name": f"Quakes under {nearest['name']} (30 days)",
                                "data": [round(m, 1) for m in mags][::-1], "unit": "M"}]}

    if score >= 60:
        headline = (f"{nearest['name']} is at {nearest.get('alert_level')} "
                    f"({nearest['distance_km']} km away). Prepare for ashfall scenarios.")
    elif nearest:
        headline = (f"Nearest monitored volcano is {nearest['name']}, "
                    f"{nearest['distance_km']} km away, alert level "
                    f"{nearest.get('alert_level', 'UNKNOWN')}.")
    else:
        headline = f"Active eruption tracked {erupting_near[0]['title']}."

    return base.result(
        "volcano", snap, score, headline=headline, kind="impact",
        confidence=0.85 if nearest else 0.6,
        factors=factors[:8],
        features={"volcano_distance_km": nearest["distance_km"] if nearest else None},
        timeline=timeline,
        map_layers={"points": volc_points, "gibs": ["aerosol"]},
        recommendations=_recommendations(score),
        impact=economics.estimate("volcano", snap.lat, snap.lon, score, radius_km=80)
               if score > 20 else None,
        sources=["USGS Volcano Hazards Program (HANS)", "NASA EONET", "USGS earthquake catalog"],
        methodology=("Live USGS alert levels and aviation color codes for every "
                     "monitored US volcano, cross-checked with global EONET eruption "
                     "events and shallow quake swarms within 30 km of the nearest "
                     "edifice. Eruptions are not predicted; readiness is scored."))


def _recommendations(score):
    recs = []
    if score >= 60:
        recs += [
            base.rec("immediate", "residents",
                     "Stock N95 masks, goggles, and plastic sheeting for ashfall",
                     "volcanic ash is pulverized glass, not smoke"),
            base.rec("high", "aviation",
                     "Monitor VAAC advisories and expect route closures",
                     "ash ingestion destroys jet engines within minutes"),
            base.rec("high", "water utilities",
                     "Cover open reservoirs and intakes before ash arrives",
                     "ash contamination is expensive to treat and settles fast"),
            base.rec("high", "public health",
                     "Message respiratory protection for children, seniors, and asthmatics",
                     "fine ash spikes respiratory admissions downwind"),
        ]
    elif score >= 30:
        recs.append(base.rec("high", "emergency managers",
                             "Review ashfall annexes and evacuation corridors with the observatory",
                             "the nearest volcano is above background activity"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "No volcanic concern for this area right now",
                             "all nearby monitored volcanoes are at normal levels"))
    return recs
