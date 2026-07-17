# infrastructure risk: rolls the other hazard modules up into what actually
# breaks, power, transport, hospitals, water, housing. runs the cheap subset
# of sibling modules and maps their scores onto system vulnerabilities.
from src.analysis import economics
from src.data.cities import nearby_cities, population_exposure
from src.modules import base

# how strongly each hazard stresses each system, 0-1, from FEMA lifeline docs
IMPACT_MATRIX = {
    #                power  roads  hospitals water  housing
    "wildfire":     (0.85,  0.70,  0.40,     0.55,  0.90),
    "flood":        (0.60,  0.90,  0.55,     0.80,  0.75),
    "heatwave":     (0.90,  0.20,  0.75,     0.35,  0.15),
    "winter":       (0.80,  0.90,  0.55,     0.40,  0.35),
    "tornado":      (0.80,  0.45,  0.50,     0.30,  0.85),
    "cyclone":      (0.95,  0.75,  0.65,     0.60,  0.85),
    "earthquake":   (0.70,  0.80,  0.85,     0.90,  0.90),
}
SYSTEMS = ["Power grid", "Transportation", "Hospitals", "Water systems", "Housing"]


def assess(snap):
    # run the sibling assessments that feed the rollup; import here to dodge cycles
    from src.modules import wildfire, flood, heatwave, winter, tornado, cyclone, earthquake
    siblings = {"wildfire": wildfire, "flood": flood, "heatwave": heatwave, "winter": winter,
                "tornado": tornado, "cyclone": cyclone, "earthquake": earthquake}
    hazard_scores = {}
    for name, mod in siblings.items():
        try:
            r = mod.assess(snap)
            if "error" not in r:
                hazard_scores[name] = r["assessment"]["score"]
        except Exception:
            continue
    if not hazard_scores:
        return {"error": "Could not evaluate underlying hazards for this location."}

    # each system's stress = max over hazards of (hazard score x coupling)
    system_scores = {}
    system_drivers = {}
    for i, system in enumerate(SYSTEMS):
        best, driver = 0.0, None
        for hz, s in hazard_scores.items():
            v = s * IMPACT_MATRIX[hz][i] / 100.0
            if v > best:
                best, driver = v, hz
        system_scores[system] = round(best * 100, 1)
        system_drivers[system] = driver

    score = max(system_scores.values())
    worst = max(system_scores, key=system_scores.get)

    factors = [base.factor(system, system_scores[system], "/100",
                           system_scores[system] / 500,
                           f"dominant stressor: {system_drivers[system]}")
               for system in SYSTEMS]

    cities = nearby_cities(snap.lat, snap.lon, 80)[:6]
    pop = population_exposure(snap.lat, snap.lon, 80)

    timeline = {"labels": SYSTEMS,
                "series": [{"name": "System stress", "kind": "bar",
                            "data": [system_scores[s] for s in SYSTEMS], "unit": "/100"}]}

    headline = (f"Most stressed system: {worst.lower()} ({system_scores[worst]:.0f}/100), "
                f"driven by {system_drivers[worst]} conditions. "
                f"About {pop:,} people depend on lifelines within 80 km.")

    return base.result(
        "infrastructure", snap, score, headline=headline, kind="impact",
        confidence=0.7,
        factors=factors,
        features={"system_scores": system_scores},
        timeline=timeline,
        map_layers={"points": [{"lat": c["lat"], "lon": c["lon"], "kind": "city",
                                "label": f"{c['name']}, {c['state']}: pop {c['population']:,}"}
                               for c in cities]},
        recommendations=_recommendations(system_scores, system_drivers),
        impact=economics.estimate("default", snap.lat, snap.lon, score, radius_km=80),
        sources=["Composite of wildfire, flood, heat, tornado, cyclone, earthquake modules",
                 "US Census city populations"],
        methodology=("FEMA community-lifelines style rollup: each hazard module's "
                     "live score is mapped through a hazard-to-system coupling matrix, "
                     "and each system reports its worst-case stressor."),
        extras={"hazard_scores": hazard_scores, "system_drivers": system_drivers})


def _recommendations(system_scores, drivers):
    recs = []
    ranked = sorted(system_scores.items(), key=lambda kv: -kv[1])
    for system, s in ranked[:2]:
        if s < 35:
            break
        if system == "Power grid":
            recs.append(base.rec("high", "utilities",
                                 f"Pre-stage crews and spares: {drivers[system]} is the active stressor",
                                 f"grid stress scores {s:.0f}/100 in current conditions"))
        elif system == "Transportation":
            recs.append(base.rec("high", "road agencies",
                                 "Identify likely closure segments and publish detours in advance",
                                 f"transport stress scores {s:.0f}/100, driven by {drivers[system]}"))
        elif system == "Hospitals":
            recs.append(base.rec("high", "hospital administrators",
                                 "Verify generator fuel and review surge staffing triggers",
                                 f"hospital stress scores {s:.0f}/100 under {drivers[system]} conditions"))
        elif system == "Water systems":
            recs.append(base.rec("high", "water utilities",
                                 "Check backup power at pumps and lift stations",
                                 f"water system stress scores {s:.0f}/100"))
        else:
            recs.append(base.rec("high", "housing agencies",
                                 "Map vulnerable housing stock against the active hazard footprint",
                                 f"housing stress scores {s:.0f}/100"))
    if not recs:
        recs.append(base.rec("advisory", "planners",
                             "All lifeline systems read low stress today. Good window for maintenance",
                             "scheduled work is safest when hazard scores are low"))
    return recs
