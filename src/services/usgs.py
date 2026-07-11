# USGS feeds: earthquake catalog, river gauges, volcano alert levels
from datetime import datetime, timedelta, timezone

from src import config
from src.http_client import fetch_json, TTL_LIVE, TTL_FORECAST, TTL_ARCHIVE


def earthquakes(lat=None, lon=None, radius_km=500, days=30, min_magnitude=2.5, limit=200):
    """quakes from the live USGS FDSN catalog, near a point or worldwide"""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "format": "geojson", "starttime": start,
        "minmagnitude": min_magnitude, "limit": limit, "orderby": "time",
    }
    if lat is not None:
        params.update({"latitude": round(lat, 3), "longitude": round(lon, 3),
                       "maxradiuskm": radius_km})
    data = fetch_json(config.USGS_EARTHQUAKES, params, ttl=TTL_LIVE if days <= 7 else TTL_FORECAST)
    quakes = []
    for f in (data or {}).get("features", []):
        p = f.get("properties", {})
        g = (f.get("geometry") or {}).get("coordinates", [None, None, None])
        quakes.append({
            "magnitude": p.get("mag"), "place": p.get("place"),
            "time": p.get("time"), "tsunami_flag": p.get("tsunami"),
            "felt": p.get("felt"), "significance": p.get("sig"),
            "lon": g[0], "lat": g[1], "depth_km": g[2],
            "url": p.get("url"), "id": f.get("id"),
        })
    return quakes


def historical_seismicity(lat, lon, radius_km=300, years=50, min_magnitude=4.0):
    """long window catalog pull used to fit Gutenberg-Richter rates"""
    start = (datetime.now(timezone.utc) - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    params = {
        "format": "geojson", "starttime": start,
        "latitude": round(lat, 3), "longitude": round(lon, 3),
        "maxradiuskm": radius_km, "minmagnitude": min_magnitude, "limit": 2000,
    }
    data = fetch_json(config.USGS_EARTHQUAKES, params, ttl=TTL_ARCHIVE, timeout=40)
    mags = []
    for f in (data or {}).get("features", []):
        m = (f.get("properties") or {}).get("mag")
        if m is not None:
            mags.append(m)
    return mags


def river_gauges(lat, lon, box_deg=0.75):
    """live stage + discharge from NWIS gauges inside a bounding box"""
    bbox = f"{round(lon - box_deg, 4)},{round(lat - box_deg, 4)},{round(lon + box_deg, 4)},{round(lat + box_deg, 4)}"
    params = {
        "format": "json", "bBox": bbox,
        "parameterCd": "00065,00060",  # 00065 gage height ft, 00060 discharge cfs
        "siteStatus": "active",
    }
    data = fetch_json(config.USGS_WATER, params, ttl=TTL_LIVE, timeout=25)
    gauges = {}
    for ts in ((data or {}).get("value") or {}).get("timeSeries", []):
        src = ts.get("sourceInfo", {})
        site = src.get("siteCode", [{}])[0].get("value")
        loc = (src.get("geoLocation") or {}).get("geogLocation", {})
        code = ts.get("variable", {}).get("variableCode", [{}])[0].get("value")
        vals = ts.get("values", [{}])[0].get("value", [])
        latest = vals[-1] if vals else None
        if not site or latest is None:
            continue
        g = gauges.setdefault(site, {
            "site": site, "name": src.get("siteName"),
            "lat": loc.get("latitude"), "lon": loc.get("longitude"),
        })
        try:
            v = float(latest.get("value"))
        except (TypeError, ValueError):
            continue
        if v <= -999:  # NWIS sentinel for missing
            continue
        if code == "00065":
            g["stage_ft"] = v
        elif code == "00060":
            g["discharge_cfs"] = v
        g["observed_at"] = latest.get("dateTime")
    return list(gauges.values())


def volcanoes():
    """every US volcano HANS monitors, with its current alert level. the feed
    has no coordinates so we join against the embedded GVP catalog."""
    from src.data.volcano_coords import lookup
    data = fetch_json(config.USGS_VOLCANOES, ttl=TTL_FORECAST, timeout=25)
    if not isinstance(data, list):
        return []
    out = []
    for v in data:
        if not isinstance(v, dict):
            continue
        name = v.get("volcano_name")
        coords = lookup(name)
        if not coords:
            continue
        out.append({
            "name": name, "lat": coords[0], "lon": coords[1],
            "alert_level": (v.get("alert_level") or "UNASSIGNED").upper(),
            "color_code": (v.get("color_code") or "UNASSIGNED").upper(),
            "id": v.get("vnum"),
            "observatory": v.get("obs_abbr"),
        })
    return out
