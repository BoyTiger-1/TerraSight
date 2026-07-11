# shared helpers every intelligence module uses to build its result
# the whole frontend renders one standard schema, defined by result() below
from datetime import datetime, timezone

from src.config import risk_band


def result(module, snap, score, headline, kind="prediction", confidence=0.75,
           factors=None, features=None, timeline=None, map_layers=None,
           recommendations=None, impact=None, sources=None, methodology="",
           extras=None):
    """assemble the standard module response. score is 0-100."""
    score = max(0.0, min(100.0, float(score)))
    label, color = risk_band(score)
    conf_label = "High" if confidence >= 0.75 else "Medium" if confidence >= 0.5 else "Low"
    out = {
        "module": module,
        "location": {"name": snap.name, "lat": snap.lat, "lon": snap.lon},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assessment": {
            "score": round(score, 1), "level": label, "color": color,
            "confidence": round(confidence, 2), "confidence_label": conf_label,
            "headline": headline, "kind": kind,
        },
        "factors": factors or [],
        "features": features or {},
        "timeline": timeline,
        "map_layers": map_layers or {},
        "recommendations": recommendations or [],
        "impact": impact,
        "data_sources": sources or [],
        "methodology": methodology,
    }
    if extras:
        out.update(extras)
    return out


def factor(name, value, unit, contribution, detail=""):
    """one row of the explainability panel. contribution is -1..1, sign says
    whether this variable pushed risk up or down."""
    return {"name": name, "value": value, "unit": unit,
            "contribution": round(float(contribution), 3), "detail": detail}


def rec(priority, audience, action, reason):
    """one actionable recommendation. priority: immediate | high | advisory"""
    return {"priority": priority, "audience": audience, "action": action, "reason": reason}


def ml_factors(explanation, labels):
    """convert registry.explain() rows into display factors.
    labels maps feature key -> (pretty name, unit, detail template)"""
    rows = []
    total = sum(abs(r["delta"]) for r in explanation) or 1.0
    for r in explanation:
        name, unit, detail = labels.get(r["feature"], (r["feature"], "", ""))
        rows.append(factor(name, r["value"], unit, r["delta"] / total * (1 if r["delta"] >= 0 else 1), detail))
    return rows


def scale(value, lo, hi):
    """clamp-map value onto 0..1 between lo and hi"""
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def percentile_of(value, population):
    """what fraction of population sits below value"""
    vals = sorted(v for v in population if v is not None)
    if not vals:
        return None
    below = sum(1 for v in vals if v < value)
    return below / len(vals)
