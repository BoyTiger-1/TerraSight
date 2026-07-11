# air quality intelligence: live pollutant concentrations and a 4-day outlook
# from the Copernicus CAMS model, with wildfire smoke attribution
from src.analysis import economics
from src.modules import base

# EPA AQI breakpoints for the headline category
AQI_BANDS = [(0, "Good"), (51, "Moderate"), (101, "Unhealthy for sensitive groups"),
             (151, "Unhealthy"), (201, "Very unhealthy"), (301, "Hazardous")]


def aqi_category(aqi):
    label = AQI_BANDS[0][1]
    for cutoff, name in AQI_BANDS:
        if aqi >= cutoff:
            label = name
    return label


def assess(snap):
    data = snap.air()
    hourly = (data or {}).get("hourly") or {}
    times = hourly.get("time", [])
    if not times:
        return {"error": "Air quality data unavailable for this location."}

    def latest(name):
        vals = hourly.get(name, [])
        # walk back from 'now' to the most recent non-null reading
        for v in reversed(vals[:25]):
            if v is not None:
                return v
        return None

    aqi = latest("us_aqi")
    pm25, pm10 = latest("pm2_5"), latest("pm10")
    ozone, no2 = latest("ozone"), latest("nitrogen_dioxide")
    dust, aod = latest("dust"), latest("aerosol_optical_depth")

    if aqi is None:
        return {"error": "Air quality data unavailable for this location."}

    # US AQI 0-500 mapped to our 0-100 scale, 200+ is already extreme territory
    score = min(100.0, aqi / 2.5)
    cat = aqi_category(aqi)

    # is this smoke? cross-reference active fires within 300 km
    fires = snap.fires_nearby()
    fire_count = len(fires["fires"])
    smoke_suspect = pm25 is not None and pm25 > 35 and fire_count > 0

    factors = [
        base.factor("US AQI", round(aqi), "", base.scale(aqi, 0, 250) * 0.5,
                    f"EPA category: {cat}"),
        base.factor("PM2.5", round(pm25, 1) if pm25 is not None else None, "ug/m3",
                    base.scale(pm25 or 0, 0, 150) * 0.4,
                    "fine particles that reach the bloodstream, WHO guideline is 15"),
        base.factor("Ozone", round(ozone, 1) if ozone is not None else None, "ug/m3",
                    base.scale(ozone or 0, 0, 240) * 0.25, "sunlight-driven smog, peaks afternoons"),
        base.factor("PM10", round(pm10, 1) if pm10 is not None else None, "ug/m3",
                    base.scale(pm10 or 0, 0, 250) * 0.15, "coarse dust and pollen"),
        base.factor("NO2", round(no2, 1) if no2 is not None else None, "ug/m3",
                    base.scale(no2 or 0, 0, 200) * 0.1, "traffic and combustion tracer"),
    ]
    if dust:
        factors.append(base.factor("Dust", round(dust, 1), "ug/m3",
                                   base.scale(dust, 0, 100) * 0.1, "transported mineral dust"))
    if smoke_suspect:
        factors.insert(0, base.factor("Wildfire smoke", f"{fire_count} fires nearby", "",
                                      0.3, f"elevated PM2.5 with active fires per {fires['source']}"))

    # 4-day AQI outlook straight off the CAMS forecast
    aqi_series = hourly.get("us_aqi", [])
    pm_series = hourly.get("pm2_5", [])
    step = 3  # every 3 hours keeps the chart light
    labels = times[::step]
    timeline = {"labels": labels, "series": [
        {"name": "US AQI", "data": [round(v) if v is not None else None for v in aqi_series[::step]], "unit": ""},
        {"name": "PM2.5", "data": [round(v, 1) if v is not None else None for v in pm_series[::step]], "unit": "ug/m3"}]}

    headline = (f"US AQI {aqi:.0f}, {cat}."
                + (f" Likely wildfire smoke: {fire_count} active fires within 300 km."
                   if smoke_suspect else "")
                + (f" PM2.5 at {pm25:.0f} ug/m3." if pm25 is not None else ""))

    return base.result(
        "air-quality", snap, score, headline=headline, confidence=0.85,
        factors=factors, features={"us_aqi": aqi, "pm2_5": pm25, "ozone": ozone},
        timeline=timeline,
        map_layers={"gibs": ["aerosol"],
                    "points": [{"lat": f["lat"], "lon": f["lon"], "kind": "fire",
                                "label": f.get("title") or "fire detection"}
                               for f in fires["fires"] if f.get("lat")][:40]},
        recommendations=_recommendations(aqi, smoke_suspect),
        impact=economics.estimate("heatwave", snap.lat, snap.lon, score, radius_km=40),
        sources=["Open-Meteo Air Quality API (CAMS)", fires["source"]],
        methodology=("Live concentrations and 4-day forecast from the Copernicus CAMS "
                     "atmospheric model, converted to the EPA US AQI scale. Smoke "
                     "attribution cross-references NASA fire detections within 300 km."))


def _recommendations(aqi, smoke):
    recs = []
    if aqi >= 200:
        recs += [
            base.rec("immediate", "residents",
                     "Stay indoors, seal gaps, run HEPA filtration or a DIY box fan filter",
                     "at this level everyone experiences health effects, not just sensitive groups"),
            base.rec("immediate", "public health",
                     "Issue shelter guidance and open clean-air centers",
                     "ER visits for asthma and cardiac events climb within hours at this AQI"),
        ]
    elif aqi >= 150:
        recs += [
            base.rec("high", "residents",
                     "Cancel outdoor exercise, wear an N95 if you must be outside",
                     "cloth masks do nothing for fine particles, N95s cut exposure about 95%"),
            base.rec("high", "schools",
                     "Move recess and sports indoors",
                     "children breathe more air per body weight than adults"),
        ]
    elif aqi >= 100:
        recs.append(base.rec("high", "sensitive groups",
                             "People with asthma, heart disease, children and seniors should limit outdoor exertion",
                             "sensitive groups react well below the general-population threshold"))
    else:
        recs.append(base.rec("advisory", "residents",
                             "Air is clean. Good day to air out the house",
                             "AQI is in the healthy range"))
    if smoke:
        recs.append(base.rec("high", "residents",
                             "Set HVAC to recirculate and replace filters with MERV-13 or better",
                             "wildfire smoke infiltrates buildings through fresh-air intakes"))
    return recs
