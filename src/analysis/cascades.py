# the cascade engine: disasters are coupled systems, not isolated scores.
# each edge below is a documented physical mechanism. when a source hazard is
# elevated, it pushes the target hazard's score up through the listed weight.
# (source, target, weight, mechanism)
COUPLINGS = [
    ("drought", "wildfire", 0.20, "drought cures vegetation into fuel"),
    ("drought", "agriculture", 0.25, "soil moisture deficit stresses crops directly"),
    ("heatwave", "drought", 0.12, "heat accelerates evaporative loss"),
    ("heatwave", "wildfire", 0.12, "heat drops fuel moisture and humidity"),
    ("heatwave", "infrastructure", 0.15, "peak cooling load stresses the grid"),
    ("cyclone", "flood", 0.35, "tropical systems deliver extreme rainfall and surge"),
    ("cyclone", "infrastructure", 0.30, "sustained winds down lines and close ports"),
    ("cyclone", "landslide", 0.15, "tropical rainfall saturates slopes"),
    ("wildfire", "air-quality", 0.30, "smoke plumes load PM2.5 downwind"),
    ("wildfire", "landslide", 0.15, "burn scars shed debris flows for years"),
    ("earthquake", "tsunami", 0.25, "undersea rupture displaces the water column"),
    ("earthquake", "landslide", 0.20, "shaking destabilizes steep slopes"),
    ("earthquake", "infrastructure", 0.25, "lifelines fail under strong shaking"),
    ("volcano", "air-quality", 0.25, "ash and SO2 degrade air downwind"),
    ("volcano", "earthquake", 0.10, "magma movement drives local seismicity"),
    ("flood", "agriculture", 0.20, "inundation drowns crops and delays planting"),
    ("flood", "infrastructure", 0.25, "floodwater closes roads and substations"),
    ("flood", "landslide", 0.15, "saturated ground fails on slopes"),
    ("tornado", "infrastructure", 0.20, "tornado tracks sever power corridors"),
    ("avalanche", "infrastructure", 0.10, "slide paths close mountain corridors"),
    ("winter", "infrastructure", 0.25, "ice loads and snow down lines and close roads"),
    ("winter", "avalanche", 0.15, "heavy snow loading builds avalanche danger"),
]

# a source only propagates once it is meaningfully elevated
ACTIVATION = 40.0


def apply(scores):
    """take {module: score} and return per-module adjustments + active edges"""
    adjusted = dict(scores)
    edges = []
    for src, dst, w, mechanism in COUPLINGS:
        s = scores.get(src)
        if s is None or dst not in adjusted:
            continue
        active = s >= ACTIVATION
        boost = w * s * (1.0 if active else 0.0) * ((s - ACTIVATION) / 60.0 + 0.4) if active else 0.0
        if active and boost > 0:
            adjusted[dst] = min(100.0, adjusted[dst] + boost)
        edges.append({"source": src, "target": dst, "weight": w,
                      "mechanism": mechanism, "active": active,
                      "boost": round(boost, 1)})
    return adjusted, edges


def run_all(snap):
    """run every module against one shared snapshot, then couple the scores"""
    from src.modules import runner
    results = runner.assess_all(snap)

    base_scores = {slug: r["assessment"]["score"]
                   for slug, r in results.items() if "error" not in r}
    adjusted, edges = apply(base_scores)

    # Record the coupling outcome alongside each module result, but leave
    # `assessment` holding what the module's own model said.
    #
    # This used to overwrite `assessment.score` with the coupled value, which
    # meant one hazard at one place had two different numbers depending on which
    # page you were looking at: the matrix showed the coupled score and the
    # module page, which scores a single module and never runs the coupling,
    # showed the model's. At Bothell that was wildfire 27.1 against wildfire 2.1.
    #
    # Overwriting was the wrong half to keep. The weights below are hand-set
    # physical judgements, not fitted parameters, and nothing in /api/validation
    # measures them -- the published AUC and Brier scores belong to the module
    # models alone. A 0.20 edge from a drought sitting at 95 adds 25 points on
    # its own, so silently folding it in lets an unvalidated heuristic
    # substitute for a validated model rather than annotate it. The coupled
    # total is still returned, per module and per edge, for callers that want to
    # show it as the separate analysis layer it is.
    from src.config import risk_band
    for slug, r in results.items():
        if "error" in r:
            continue
        before = r["assessment"]["score"]
        after = round(adjusted.get(slug, before), 1)
        label, color = risk_band(after)
        incoming = [e for e in edges if e["target"] == slug and e["active"] and e["boost"] > 0]
        outgoing = [e for e in edges if e["source"] == slug and e["active"]]
        r["cascades"] = {"score_before": before, "score_after": after,
                         "level_after": label, "color_after": color,
                         "incoming": incoming, "outgoing": outgoing}

    return {"results": results, "edges": edges,
            "scores": {s: round(adjusted.get(s, v), 1) for s, v in base_scores.items()}}
