# tsunami intelligence: honestly framed as impact modeling. we cannot predict
# tsunamis, but we can measure how exposed a coastline is and whether recent
# undersea seismicity could generate one
from src.analysis import economics
from src.modules import base
from src.services import noaa, usgs


def assess(snap):
    terr = snap.terrain() or {}
    elev = terr.get("elevation", 9999)

    # distance to the nearest ocean model cell doubles as distance-to-coast
    marine = snap.marine()
    coast_km = None
    if marine and marine.get("latitude") is not None:
        coast_km = noaa.haversine_km(snap.lat, snap.lon,
                                     marine["latitude"], marine["longitude"])

    # recent large, shallow, offshore quakes are the generation signal
    quakes = usgs.earthquakes(snap.lat, snap.lon, radius_km=1500, days=30, min_magnitude=6.0)
    generators = [q for q in quakes
                  if (q.get("magnitude") or 0) >= 6.5 and (q.get("depth_km") or 999) < 100]
    flagged = [q for q in quakes if q.get("tsunami_flag")]

    alerts = noaa.alerts_matching(snap.alerts(), ["tsunami"])

    # exposure: low elevation near the coast is what kills
    coastal = coast_km is not None and coast_km < 25
    elev_f = 1 - base.scale(elev, 2, 30)
    coast_f = 1 - base.scale(coast_km if coast_km is not None else 999, 0, 25)
    exposure = elev_f * 0.6 + coast_f * 0.4 if coastal else 0.0

    seismic = base.scale(len(generators), 0, 3) * 0.5 + (0.5 if flagged else 0)
    score = exposure * 45 + min(seismic, 1.0) * exposure * 40
    if alerts:
        score = max(score, 85)

    factors = [
        base.factor("Elevation above sea level", round(elev, 1), "m",
                    elev_f * 0.4 if coastal else -0.3,
                    "runup rarely exceeds 30 m even in extreme events"),
        base.factor("Distance to open water", round(coast_km) if coast_km is not None else None, "km",
                    coast_f * 0.3 if coastal else -0.3,
                    "estimated from the nearest ocean model cell"),
        base.factor("M6.5+ shallow quakes, 30 days, 1500 km", len(generators), "",
                    base.scale(len(generators), 0, 3) * 0.2,
                    "shallow offshore thrust quakes are the main generators"),
    ]
    if flagged:
        factors.insert(0, base.factor("USGS tsunami-flagged event", flagged[0]["place"], "",
                                      0.4, f"M{flagged[0]['magnitude']} triggered tsunami messaging"))
    if alerts:
        factors.insert(0, base.factor("TSUNAMI ALERT", alerts[0].get("event"), "", 1.0,
                                      alerts[0].get("headline") or "move inland and uphill now"))

    if alerts:
        headline = f"{alerts[0]['event']} in effect. Move away from the shore immediately."
    elif not coastal:
        headline = (f"This location is effectively not tsunami-exposed: "
                    f"{elev:.0f} m elevation, about {coast_km:.0f} km from open water."
                    if coast_km is not None else
                    f"This location is far inland at {elev:.0f} m elevation. Tsunami exposure is negligible.")
    else:
        headline = (f"Coastal location {elev:.0f} m above sea level. "
                    f"{len(generators)} potential generator quake(s) in the region this month. "
                    "No active tsunami messages.")

    quake_points = [{"lat": q["lat"], "lon": q["lon"], "kind": "quake",
                     "label": f"M{q['magnitude']} {q['place']}, depth {q['depth_km']:.0f} km"}
                    for q in quakes[:30] if q.get("lat")]

    mags = [q["magnitude"] for q in quakes if q.get("magnitude")]
    timeline = {"labels": [q["place"][:26] for q in quakes[:15]][::-1],
                "series": [{"name": "Regional M6+ quakes (30 days)",
                            "data": [round(m, 1) for m in mags[:15]][::-1], "unit": "M"}]} if mags else None

    return base.result(
        "tsunami", snap, score, headline=headline, kind="impact",
        confidence=0.8 if coast_km is not None else 0.55,
        factors=factors, features={"elevation": elev, "coast_km": coast_km},
        timeline=timeline,
        map_layers={"points": quake_points},
        recommendations=_recommendations(score, coastal, bool(alerts)),
        impact=economics.estimate("tsunami", snap.lat, snap.lon, score, radius_km=20)
               if coastal else None,
        sources=["USGS earthquake catalog", "NOAA NWS tsunami alerts",
                 "Open-Meteo elevation + marine grid"],
        methodology=("Impact model, not a prediction: tsunami generation cannot be "
                     "forecast. Exposure combines elevation and coastal proximity; the "
                     "generation signal counts recent shallow M6.5+ regional seismicity "
                     "and live NOAA tsunami messages."))


def _recommendations(score, coastal, alert):
    recs = []
    if alert:
        recs.append(base.rec("immediate", "everyone near the coast",
                             "Move inland or to ground above 30 m on foot if possible, do not wait to see the wave",
                             "the first surge can arrive in minutes after a local quake"))
    elif coastal:
        recs += [
            base.rec("high", "residents",
                     "Learn your evacuation route uphill and practice it once, natural warning is strong shaking",
                     "if the ground shakes hard for 20+ seconds at the coast, evacuate without waiting for sirens"),
            base.rec("advisory", "hotels and coastal businesses",
                     "Post evacuation maps and drill staff, especially for guests",
                     "most tsunami casualties are people unfamiliar with the shore"),
            base.rec("advisory", "planners",
                     "Keep critical facilities out of the runup zone shown on the map",
                     "hospitals and generators below 15 m elevation multiply disaster impacts"),
        ]
    else:
        recs.append(base.rec("advisory", "residents",
                             "No tsunami exposure at this elevation and distance from the coast",
                             "no action needed"))
    return recs
