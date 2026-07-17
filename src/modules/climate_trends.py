# climate trends: how much this exact location has warmed since 1950 and where
# CMIP6 models take it by 2050. trend fitting on real reanalysis, no vibes.
from src.modules import base


def _annual_means(times, tmax, tmin):
    """collapse daily records into annual mean temperature"""
    years = {}
    for t, hi, lo in zip(times, tmax, tmin):
        if hi is None or lo is None:
            continue
        y = t[:4]
        acc = years.setdefault(y, [0.0, 0])
        acc[0] += (hi + lo) / 2
        acc[1] += 1
    return {y: v[0] / v[1] for y, v in years.items() if v[1] >= 300}


def _trend(xs, ys):
    """ordinary least squares slope per decade"""
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs) or 1
    return num / den * 10


def _unavailable(snap):
    """soft result when the ERA5 archive is temporarily rate-limited, so the
    module still appears (honestly) instead of failing the whole assessment"""
    return base.result(
        "climate-trends", snap, 0.0, kind="monitoring", confidence=0.2,
        headline=("Long-term climate trend is temporarily unavailable: the ERA5 archive "
                  "is rate-limited right now. Everything else assessed normally; try this "
                  "module again in a few minutes."),
        factors=[], sources=["Open-Meteo ERA5 archive (rate-limited)"],
        methodology=("This module needs 15 years of daily reanalysis to fit a local "
                     "warming trend. The free archive API has an hourly request limit; "
                     "when it is exhausted the trend cannot be computed until it resets."))


def assess(snap):
    clim = snap.climatology()
    daily = (clim or {}).get("daily") or {}
    if not daily.get("time"):
        return _unavailable(snap)

    annual = _annual_means(daily["time"], daily["temperature_2m_max"], daily["temperature_2m_min"])
    years = sorted(annual.keys())
    if len(years) < 10:
        return _unavailable(snap)

    xs = [int(y) for y in years]
    ys = [annual[y] for y in years]
    slope = _trend(xs, ys)  # degrees C per decade
    baseline = sum(ys[:10]) / 10
    recent = sum(ys[-10:]) / 10
    warming = recent - baseline

    # annual precipitation trend from the same archive
    pr_years = {}
    for t, v in zip(daily["time"], daily["precipitation_sum"]):
        pr_years.setdefault(t[:4], []).append(v or 0)
    pr_annual = {y: sum(v) for y, v in pr_years.items() if len(v) >= 300}
    pr_slope = _trend([int(y) for y in sorted(pr_annual)],
                      [pr_annual[y] for y in sorted(pr_annual)])

    # CMIP6 projection: mean tmax by 5-year bucket out to 2050
    proj = snap.climate_projection()
    pd = (proj or {}).get("daily") or {}
    proj_buckets = {}
    for t, v in zip(pd.get("time", []), pd.get("temperature_2m_max", [])):
        if v is None:
            continue
        bucket = f"{int(t[:4]) // 5 * 5}"
        acc = proj_buckets.setdefault(bucket, [0.0, 0])
        acc[0] += v
        acc[1] += 1
    proj_series = {b: round(v[0] / v[1], 2) for b, v in sorted(proj_buckets.items()) if v[1] > 200}

    # score reflects how fast the local climate is shifting, not a hazard per se
    score = base.scale(abs(slope or 0), 0.05, 0.6) * 100
    direction = "warming" if (slope or 0) > 0 else "cooling"

    factors = [
        base.factor("Temperature trend", round(slope, 3) if slope else None, "C/decade",
                    base.scale(abs(slope or 0), 0.05, 0.6) * 0.6,
                    "least-squares fit over the 30-year record"),
        base.factor("Change vs early baseline", round(warming, 2), "C",
                    base.scale(abs(warming), 0.2, 2.0) * 0.4,
                    "last 10 years vs first 10 years of the record"),
        base.factor("Precipitation trend", round(pr_slope, 1) if pr_slope else None, "mm/decade",
                    0.0, "annual total, positive means wetter"),
        base.factor("Hottest year in record",
                    years[ys.index(max(ys))], "",
                    0.0, f"annual mean {max(ys):.1f} C"),
    ]

    timeline = {"labels": years, "series": [
        {"name": "Annual mean temperature", "data": [round(v, 2) for v in ys], "unit": "C"}]}

    proj_note = ""
    if proj_series:
        first, last = list(proj_series.values())[0], list(proj_series.values())[-1]
        proj_note = f" CMIP6 models project daytime highs shifting another {last - first:+.1f} C by 2050."

    headline = (f"This location has {direction.replace('ing', 'ed')} {abs(warming):.1f} C "
                f"({slope:+.2f} C per decade) over the modern record.{proj_note}")

    return base.result(
        "climate-trends", snap, score, headline=headline, kind="monitoring",
        confidence=0.9,
        factors=factors, features={"trend_c_per_decade": slope, "warming_c": warming},
        timeline=timeline,
        map_layers={"gibs": ["temp"]},
        recommendations=_recommendations(slope or 0, pr_slope or 0),
        impact=None,
        sources=["Open-Meteo ERA5 archive (1990s-present)", "Open-Meteo Climate API (CMIP6)"],
        methodology=("Annual means computed from 30 years of daily ERA5 reanalysis at "
                     "this point, trend fit by ordinary least squares. Projections are "
                     "multi-model CMIP6 daily maxima averaged in 5-year buckets to 2050."),
        extras={"projection": proj_series})


def _recommendations(slope, pr_slope):
    recs = [
        base.rec("advisory", "planners",
                 "Use the per-decade trend, not historical averages, when sizing drainage and cooling",
                 "infrastructure designed to yesterday's climate underperforms on day one"),
    ]
    if slope > 0.2:
        recs.append(base.rec("high", "governments",
                             "Update heat action plans: this location is warming faster than the global mean",
                             f"local trend is {slope:.2f} C per decade"))
    if pr_slope < -20:
        recs.append(base.rec("high", "water utilities",
                             "Model supply against the declining precipitation trend",
                             f"annual rainfall is falling about {abs(pr_slope):.0f} mm per decade"))
    elif pr_slope > 20:
        recs.append(base.rec("high", "planners",
                             "Re-run stormwater capacity studies with the wetter trend",
                             f"annual rainfall is rising about {pr_slope:.0f} mm per decade"))
    recs.append(base.rec("advisory", "researchers",
                         "Download the annual series from this panel for attribution work",
                         "point-level ERA5 series are suitable for local trend studies"))
    return recs
