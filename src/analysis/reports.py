# the executive report generator: turns the full multi-hazard assessment into
# the kind of situation report an emergency management agency would circulate.
# plain professional language assembled from the actual numbers, no fluff.
from datetime import datetime, timezone

from src.analysis import cascades
from src.modules import MODULES, module_meta


def _para_conditions(results):
    """opening paragraph describing the environment right now"""
    bits = []
    wf = results.get("wildfire")
    if wf and "error" not in wf:
        f = wf.get("features", {})
        bits.append(f"maximum temperatures near {f.get('tmax_c', '?')} C with minimum "
                    f"relative humidity around {f.get('rh_min_pct', '?')} percent")
        if f.get("days_since_rain") is not None:
            bits.append(f"{f['days_since_rain']} days since the last wetting rain")
    aq = results.get("air-quality")
    if aq and "error" not in aq:
        bits.append(f"US AQI at {aq['features'].get('us_aqi', '?'):.0f}")
    dr = results.get("drought")
    if dr and "error" not in dr:
        bits.append(dr.get("category", "").lower() or "no drought designation")
    if not bits:
        return "Environmental data retrieval was incomplete for this location."
    return ("Current observations show " + ", ".join(bits[:-1])
            + (", and " + bits[-1] if len(bits) > 1 else bits[0]) + ".")


def _rank(results):
    rows = []
    for slug, r in results.items():
        if "error" in r:
            continue
        a = r["assessment"]
        rows.append({"module": slug, "title": MODULES[slug]["title"], "score": a["score"],
                     "level": a["level"], "confidence": a["confidence_label"],
                     "headline": a["headline"], "kind": a["kind"]})
    rows.sort(key=lambda x: -x["score"])
    return rows


def generate(snap):
    """full executive report as structured sections the frontend lays out"""
    bundle = cascades.run_all(snap)
    results = bundle["results"]
    ranked = _rank(results)
    now = datetime.now(timezone.utc)

    # monitoring modules (like climate trends) describe pace of change, not an
    # acute hazard, so they stay out of the "elevated hazards" headline count
    hazards_only = [r for r in ranked if r["kind"] != "monitoring"]
    elevated = [r for r in hazards_only if r["score"] >= 50]
    top = hazards_only[0] if hazards_only else None

    # executive summary paragraph
    if not ranked:
        summary = "Data retrieval failed across modules; this report is incomplete."
    elif not elevated:
        summary = (f"No hazard reaches the elevated threshold at {snap.name} at this time. "
                   f"The leading concern is {top['title'].lower()} at {top['score']:.0f}/100 "
                   f"({top['level'].lower()}). Routine monitoring is sufficient, and the "
                   "preparedness actions listed below can be scheduled at convenience.")
    else:
        names = ", ".join(f"{r['title'].lower()} ({r['score']:.0f}/100, {r['level'].lower()})"
                          for r in elevated[:4])
        summary = (f"This assessment identifies {len(elevated)} hazard(s) at elevated levels "
                   f"for {snap.name}: {names}. "
                   + ("Cross-hazard coupling is active and has raised downstream risks, "
                      "detailed in the cascade section. " if any(
                          e["active"] and e["boost"] > 0 for e in bundle["edges"]) else "")
                   + "Recommended actions are prioritized below by urgency and audience.")

    # per-hazard sections for everything scoring above 25, plus the top three regardless
    keep = {r["module"] for r in ranked[:3]} | {r["module"] for r in ranked if r["score"] >= 25}
    hazard_sections = []
    for r in ranked:
        if r["module"] not in keep:
            continue
        full = results[r["module"]]
        drivers = [f for f in full.get("factors", [])[:3]]
        driver_text = "; ".join(
            f"{f['name']} at {f['value']}{(' ' + f['unit']) if f['unit'] else ''}"
            for f in drivers if f.get("value") is not None)
        cascade_in = (full.get("cascades") or {}).get("incoming") or []
        hazard_sections.append({
            "module": r["module"], "title": r["title"], "score": r["score"],
            "level": r["level"], "kind": r["kind"], "confidence": r["confidence"],
            "narrative": full["assessment"]["headline"],
            "drivers": driver_text,
            "cascade_note": ("; ".join(f"{c['mechanism']} (+{c['boost']})"
                                       for c in cascade_in) if cascade_in else None),
            "methodology": full.get("methodology", ""),
        })

    # roll every module's recommendations into one prioritized action plan
    actions = {"immediate": [], "high": [], "advisory": []}
    seen = set()
    for r in ranked:
        for rec in results[r["module"]].get("recommendations", []):
            key = rec["action"][:60]
            if key in seen:
                continue
            seen.add(key)
            actions[rec["priority"]].append({**rec, "hazard": r["title"]})
    for k in actions:
        actions[k] = actions[k][:8]

    # economic exposure rollup from the modules that computed one
    exposures = []
    for r in ranked[:6]:
        imp = results[r["module"]].get("impact")
        if imp and r["score"] >= 25:
            exposures.append({"hazard": r["title"], "loss": imp["expected_loss_label"],
                              "population": imp["population_exposed"],
                              "radius_km": imp["radius_km"]})

    active_edges = [e for e in bundle["edges"] if e["active"] and e["boost"] > 0]

    sources = sorted({s for r in results.values() if "error" not in r
                      for s in r.get("data_sources", [])})

    return {
        "title": f"Multi-Hazard Intelligence Assessment: {snap.name}",
        "generated_at": now.isoformat(),
        "generated_date": now.strftime("%B %d, %Y at %H:%M UTC"),
        "location": {"name": snap.name, "lat": snap.lat, "lon": snap.lon},
        "summary": summary,
        "conditions": _para_conditions(results),
        "risk_table": ranked,
        "hazard_sections": hazard_sections,
        "cascades": active_edges,
        "actions": actions,
        "exposures": exposures,
        "data_sources": sources,
        "confidence_note": (
            "Confidence labels reflect model cross-validation performance and live "
            "data completeness at generation time. Impact figures are planning "
            "estimates accurate to order of magnitude, not underwriting values. "
            "Earthquake, tsunami, and volcanic modules assess impact and readiness; "
            "science cannot currently predict those events and this platform does "
            "not claim to."),
        "module_meta": module_meta(),
    }
