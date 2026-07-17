# the cascade engine: disasters are coupled systems, not isolated scores.
# each edge below is a documented physical mechanism. when a source hazard is
# elevated, it pushes the target hazard's score up through the listed weight.
from src.modules import MODULES

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
    results = {}
    for slug, meta in MODULES.items():
        try:
            r = meta["impl"].assess(snap)
        except Exception as e:
            r = {"error": f"{type(e).__name__}: {e}"}
        results[slug] = r

    base_scores = {slug: r["assessment"]["score"]
                   for slug, r in results.items() if "error" not in r}
    adjusted, edges = apply(base_scores)

    # write the coupling outcome back onto each module result
    for slug, r in results.items():
        if "error" in r:
            continue
        before = r["assessment"]["score"]
        after = round(adjusted.get(slug, before), 1)
        incoming = [e for e in edges if e["target"] == slug and e["active"] and e["boost"] > 0]
        outgoing = [e for e in edges if e["source"] == slug and e["active"]]
        r["cascades"] = {"score_before": before, "score_after": after,
                         "incoming": incoming, "outgoing": outgoing}
        if after != before:
            from src.config import risk_band
            label, color = risk_band(after)
            r["assessment"]["score"] = after
            r["assessment"]["level"] = label
            r["assessment"]["color"] = color

    return {"results": results, "edges": edges,
            "scores": {s: round(adjusted.get(s, v), 1) for s, v in base_scores.items()}}
