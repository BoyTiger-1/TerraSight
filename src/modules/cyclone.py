# tropical cyclone intelligence: live NHC storm positions, a Rankine-style wind
# field to estimate local winds, sea surface temperature as fuel, and the
# cascade into surge, flood, and outage risk
from src.analysis import economics
from src.modules import base

# Saffir-Simpson categories from sustained wind in knots
SS_CATS = [(137, "Category 5"), (113, "Category 4"), (96, "Category 3"),
           (83, "Category 2"), (64, "Category 1"), (34, "Tropical Storm"),
           (0, "Tropical Depression")]


def ss_category(kt):
    for cutoff, name in SS_CATS:
        if (kt or 0) >= cutoff:
            return name
    return "Tropical Depression"


def local_wind_kt(storm_kt, distance_km, rmw_km=45):
    """crude Rankine vortex decay: full intensity inside the radius of maximum
    winds, then falling off with distance^0.6 outside it"""
    if distance_km <= rmw_km:
        return storm_kt
    return storm_kt * (rmw_km / distance_km) ** 0.6


def quick(env):
    """simulator: what if the storm were closer/stronger or seas warmer"""
    dist = env.get("storm_distance_km")
    kt = env.get("storm_intensity_kt", 0) * env.get("wind_mult", 1.0)
    if dist is None:
        # no storm: potential-only score from SST
        sst = env.get("sst_c")
        return round(base.scale(sst or 20, 26, 31) * 35, 1) if sst else 0.0
    wind_here = local_wind_kt(kt, max(dist, 10))
    return round(min(base.scale(wind_here, 20, 100) * 100, 100), 1)


def assess(snap):
    storms = snap.storms()
    marine = snap.marine()
    mh = (marine or {}).get("hourly") or {}
    sst_vals = [v for v in mh.get("sea_surface_temperature", []) if v is not None]
    sst = sst_vals[0] if sst_vals else None
    wave_vals = [v for v in mh.get("wave_height", []) if v is not None]
    wave_max = max(wave_vals) if wave_vals else None

    nearest = None
    for s in storms:
        if s.get("lat") is None:
            continue
        d = snap.distance_to(s["lat"], s["lon"])
        if nearest is None or d < nearest["distance_km"]:
            nearest = {**s, "distance_km": round(d)}

    from src.services import noaa as noaa_svc
    alerts = noaa_svc.alerts_matching(snap.alerts(),
                                      ["hurricane", "tropical storm", "storm surge", "typhoon"])

    factors = []
    if nearest:
        wind_here = local_wind_kt(nearest["intensity_kt"] or 0, max(nearest["distance_km"], 10))
        score = min(base.scale(wind_here, 20, 100) * 100, 100)
        cat = ss_category(nearest["intensity_kt"])
        headline = (f"{cat} {nearest['name']} is {nearest['distance_km']} km away, "
                    f"sustained {nearest['intensity_kt']} kt. Estimated local winds "
                    f"{wind_here:.0f} kt if the track holds.")
        factors += [
            base.factor("Distance to storm center", nearest["distance_km"], "km",
                        base.scale(1500 - nearest["distance_km"], 0, 1500) * 0.4,
                        f"{nearest['name']}, moving {nearest.get('movement_dir', '?')} at {nearest.get('movement_speed_kt', '?')} kt"),
            base.factor("Storm intensity", nearest["intensity_kt"], "kt",
                        base.scale(nearest["intensity_kt"] or 0, 30, 140) * 0.35, cat),
            base.factor("Central pressure", nearest.get("pressure_mb"), "mb",
                        base.scale(1010 - (nearest.get("pressure_mb") or 1010), 0, 90) * 0.15,
                        "deeper pressure, stronger storm"),
        ]
    else:
        # no active storm: report formation potential from ocean heat
        potential = base.scale(sst or 20, 26, 31) * 35 if sst else 0
        score = potential
        headline = ("No active tropical cyclones threaten this location. "
                    + (f"Nearby sea surface is {sst:.1f} C"
                       + (", warm enough to fuel development." if sst >= 26.5 else ", below the 26.5 C development threshold.")
                       if sst is not None else "No marine data for this point."))

    if sst is not None:
        factors.append(base.factor("Sea surface temperature", round(sst, 1), "C",
                                   base.scale(sst, 26, 31) * 0.2,
                                   "cyclones need 26.5 C or warmer to intensify"))
    if wave_max is not None:
        factors.append(base.factor("Peak wave height, 5 days", round(wave_max, 1), "m",
                                   base.scale(wave_max, 2, 9) * 0.15, "swell arrives before the storm"))
    if alerts:
        score = max(score, 75)
        factors.insert(0, base.factor("NWS tropical alert", alerts[0].get("event"), "", 0.4,
                                      alerts[0].get("headline") or ""))

    storm_points = [{"lat": s["lat"], "lon": s["lon"], "kind": "storm",
                     "label": f"{ss_category(s['intensity_kt'])} {s['name']}, {s['intensity_kt']} kt"}
                    for s in storms if s.get("lat") is not None]

    timeline = None
    if wave_vals:
        step = 6
        timeline = {"labels": mh.get("time", [])[::step], "series": [
            {"name": "Wave height", "data": [round(v, 2) if v is not None else None for v in mh.get("wave_height", [])[::step]], "unit": "m"},
            {"name": "Sea surface temp", "data": [round(v, 1) if v is not None else None for v in mh.get("sea_surface_temperature", [])[::step]], "unit": "C"}]}

    return base.result(
        "cyclone", snap, score, headline=headline,
        confidence=0.85 if nearest else 0.7,
        factors=factors,
        features={"storm_distance_km": nearest["distance_km"] if nearest else None,
                  "storm_intensity_kt": nearest["intensity_kt"] if nearest else 0,
                  "sst_c": sst},
        timeline=timeline,
        map_layers={"points": storm_points, "gibs": ["clouds"]},
        recommendations=_recommendations(score, nearest, alerts),
        impact=economics.estimate("cyclone", snap.lat, snap.lon, score, radius_km=100),
        sources=["NOAA NHC CurrentStorms", "Open-Meteo Marine API", "NOAA NWS alerts"],
        methodology=("Live National Hurricane Center storm positions with a Rankine "
                     "vortex decay estimate of winds at your location, plus sea surface "
                     "temperature as intensification fuel. When a storm threatens, this "
                     "module also drives the flood, infrastructure, and outage cascades."),
        extras={"active_storms": storms})


def _recommendations(score, nearest, alerts):
    recs = []
    if score >= 75:
        recs += [
            base.rec("immediate", "residents",
                     "Follow local evacuation orders, especially in surge zones. Leave early",
                     "storm surge, not wind, causes about half of US hurricane deaths"),
            base.rec("immediate", "emergency managers",
                     "Activate EOC, verify shelter generators and fuel contracts",
                     "landfall logistics lock up 24-48 hours ahead of arrival"),
            base.rec("high", "utilities",
                     "Stage line crews outside the forecast wind field",
                     "restoration speed depends on crews surviving the storm untouched"),
            base.rec("high", "hospitals",
                     "Test generator transfer switches and discharge stable patients now",
                     "facilities in the wind field lose municipal power in most landfalls"),
        ]
    elif score >= 40:
        recs += [
            base.rec("high", "residents",
                     "Refuel vehicles, refill prescriptions, secure loose outdoor items",
                     "supply chains tighten fast once watches are posted"),
            base.rec("advisory", "businesses",
                     "Review continuity plans and back up on-premise systems offsite",
                     "a track shift of 100 km changes everything, prepare while it is cheap"),
        ]
    elif nearest:
        recs.append(base.rec("advisory", "residents",
                             f"Track {nearest['name']} once daily via the NHC",
                             "the storm is distant but tracks change"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "No tropical threat. A calm week to review your hurricane kit",
                             "preparation before the season beats preparation during a watch"))
    return recs
