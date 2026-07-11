# earthquake impact assessment: honestly framed. nobody can predict earthquakes,
# so this module measures real seismicity rates (Gutenberg-Richter), estimates
# shaking from recent events, and models aftershock odds and losses
import math
from datetime import datetime, timezone

from src.analysis import economics
from src.modules import base


def gutenberg_richter(mags, min_mag=4.0):
    """fit the b-value and annual rate of M5+ from a historical catalog.
    b ~ 1 worldwide; the a-value sets how active this region is."""
    if len(mags) < 20:
        return None
    n = len(mags)
    mean_m = sum(mags) / n
    # Aki maximum-likelihood estimator
    b = 1.0 / (math.log(10) * max(mean_m - min_mag, 0.05))
    return {"b_value": round(b, 2), "n_events": n}


def mmi_from_quake(mag, dist_km):
    """intensity at a distance, simplified atkinson-wald style attenuation"""
    if dist_km < 1:
        dist_km = 1
    mmi = 3.66 + 1.66 * mag - 3.31 * math.log10(dist_km) - 1.5
    return max(min(mmi, 12), 1)


def aftershock_probability(mainshock_mag, days_since):
    """Reasenberg-Jones style: chance of an M5+ aftershock in the next 7 days"""
    if days_since < 0:
        return 0
    rate = 10 ** (mainshock_mag - 5.0 - 1.0) / (days_since + 0.5)  # Omori decay
    return 1 - math.exp(-rate * 7)


def assess(snap):
    recent = snap.quakes(radius_km=300, days=30, min_mag=2.5)
    from src.services import usgs
    historical = usgs.historical_seismicity(snap.lat, snap.lon, radius_km=300, years=50)
    gr = gutenberg_richter(historical)

    # strongest recent event and the shaking it produced here
    strongest, mmi_here, days_since = None, 0, None
    for q in recent:
        if q.get("magnitude") is None or q.get("lat") is None:
            continue
        d = snap.distance_to(q["lat"], q["lon"])
        m = mmi_from_quake(q["magnitude"], max(d, 5))
        if strongest is None or m > mmi_here:
            strongest, mmi_here = q, m
            days_since = (datetime.now(timezone.utc).timestamp() * 1000 - q["time"]) / 86400000

    # regional activity: how many M5+ per year does this area actually produce
    m5_per_year = sum(1 for m in historical if m >= 5.0) / 50.0

    aftershock_p = 0
    if strongest and strongest["magnitude"] >= 5.0 and days_since is not None:
        aftershock_p = aftershock_probability(strongest["magnitude"], days_since)

    activity = base.scale(m5_per_year, 0.02, 2.0)
    recent_shake = base.scale(mmi_here, 3, 8)
    score = (activity * 0.5 + recent_shake * 0.3 + base.scale(aftershock_p, 0, 0.5) * 0.2) * 100

    factors = [
        base.factor("Regional M5+ rate", round(m5_per_year, 2), "per year",
                    activity * 0.5, "from 50 years of USGS catalog within 300 km"),
        base.factor("Strongest recent shaking here",
                    f"MMI {mmi_here:.1f}" if strongest else "none", "",
                    recent_shake * 0.3,
                    f"from M{strongest['magnitude']} {strongest['place']}" if strongest else
                    "no significant events within 300 km this month"),
        base.factor("M5+ aftershock odds, 7 days", round(aftershock_p * 100), "%",
                    base.scale(aftershock_p, 0, 0.5) * 0.2,
                    "Omori-law decay from the largest recent event"),
    ]
    if gr:
        factors.append(base.factor("Gutenberg-Richter b-value", gr["b_value"], "",
                                   0.0, f"fit on {gr['n_events']} historical events; "
                                        "lower b means relatively more large quakes"))

    quake_points = [{"lat": q["lat"], "lon": q["lon"], "kind": "quake",
                     "label": f"M{q['magnitude']} {q['place']}"}
                    for q in recent[:50] if q.get("lat")]

    mags_recent = [q["magnitude"] for q in recent if q.get("magnitude")][:20]
    timeline = {"labels": [q["place"][:24] for q in recent[:20]][::-1],
                "series": [{"name": "Recent quakes (30 days, 300 km)",
                            "data": [round(m, 1) for m in mags_recent][::-1], "unit": "M"}]} if mags_recent else None

    if strongest and mmi_here >= 5:
        headline = (f"M{strongest['magnitude']} near {strongest['place']} produced an "
                    f"estimated MMI {mmi_here:.0f} here. "
                    f"{aftershock_p * 100:.0f}% chance of an M5+ aftershock this week.")
    else:
        level = "active" if m5_per_year > 0.5 else "moderate" if m5_per_year > 0.1 else "quiet"
        headline = (f"Seismically {level} region: about {m5_per_year:.1f} M5+ events per "
                    f"year within 300 km. {len(recent)} quakes recorded this month.")

    return base.result(
        "earthquake", snap, score, headline=headline, kind="impact",
        confidence=0.85 if gr else 0.6,
        factors=factors,
        features={"m5_rate": m5_per_year, "recent_mmi": mmi_here},
        timeline=timeline,
        map_layers={"points": quake_points},
        recommendations=_recommendations(score, aftershock_p),
        impact=economics.estimate("earthquake", snap.lat, snap.lon, score, radius_km=60),
        sources=["USGS FDSN earthquake catalog (live + 50-year history)"],
        methodology=("Impact assessment, not prediction: no science can predict "
                     "earthquakes. Regional hazard comes from 50 years of real catalog "
                     "data (Gutenberg-Richter rates), shaking estimates use magnitude-"
                     "distance attenuation, and aftershock odds follow Omori-law decay "
                     "of the largest recent event."))


def _recommendations(score, aftershock_p):
    recs = []
    if aftershock_p > 0.2:
        recs += [
            base.rec("immediate", "residents",
                     "Expect aftershocks: stay out of visibly damaged buildings",
                     f"about {aftershock_p * 100:.0f}% odds of an M5+ aftershock this week"),
            base.rec("high", "building officials",
                     "Prioritize rapid safety tagging of damaged structures",
                     "aftershocks collapse buildings the mainshock only weakened"),
        ]
    if score >= 50:
        recs += [
            base.rec("high", "residents",
                     "Secure water heaters and tall furniture, keep 3 days of water",
                     "in active seismic zones, non-structural hazards cause most injuries"),
            base.rec("high", "governments",
                     "Fund seismic retrofits for soft-story and unreinforced masonry stock",
                     "these two building types dominate US earthquake deaths"),
        ]
    elif score >= 25:
        recs.append(base.rec("advisory", "residents",
                             "Know 'drop, cover, hold on' and keep shoes by the bed",
                             "moderate seismicity regions still produce damaging quakes"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "Low regional seismicity. Standard preparedness is sufficient",
                             "the 50-year catalog shows few significant events here"))
    recs.append(base.rec("advisory", "insurers",
                         "Use the regional M5+ rate for portfolio pricing, not recent quiet",
                         "seismic risk is stationary on decade scales, recent silence is not safety"))
    return recs
