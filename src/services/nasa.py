# NASA feeds: EONET natural event tracker and FIRMS active fire detections
import csv
import io
from datetime import datetime, timezone

from src import config
from src.http_client import fetch_json, fetch_text, TTL_LIVE, TTL_FORECAST


def eonet_events(category=None, days=30, status="open", limit=300):
    """curated natural events (wildfires, storms, volcanoes, floods...) with geometry"""
    params = {"status": status, "days": days, "limit": limit}
    if category:
        params["category"] = category
    data = fetch_json(config.EONET_EVENTS, params, ttl=TTL_FORECAST, timeout=30)
    events = []
    for e in (data or {}).get("events", []):
        geom = e.get("geometry") or []
        last = geom[-1] if geom else {}
        coords = last.get("coordinates") or [None, None]
        # points come as [lon, lat], polygons as nested rings, take the first vertex
        if isinstance(coords[0], list):
            coords = coords[0][0] if isinstance(coords[0][0], list) else coords[0]
        events.append({
            "id": e.get("id"), "title": e.get("title"),
            "category": (e.get("categories") or [{}])[0].get("id"),
            "lon": coords[0], "lat": coords[1],
            "date": last.get("date"), "magnitude": last.get("magnitudeValue"),
            "magnitude_unit": last.get("magnitudeUnit"),
            "sources": [s.get("url") for s in e.get("sources", [])][:2],
            "closed": e.get("closed"),
        })
    return events


def firms_fires(lat, lon, radius_deg=1.5, days=2):
    """VIIRS fire detections near a point, needs a free FIRMS map key"""
    if not config.FIRMS_MAP_KEY:
        return None  # caller falls back to EONET wildfire events
    area = f"{round(lon - radius_deg, 2)},{round(lat - radius_deg, 2)},{round(lon + radius_deg, 2)},{round(lat + radius_deg, 2)}"
    url = f"{config.FIRMS_AREA}/{config.FIRMS_MAP_KEY}/VIIRS_SNPP_NRT/{area}/{days}"
    text = fetch_text(url, ttl=TTL_LIVE, timeout=30)
    if not text or text.startswith("Invalid"):
        return None
    fires = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            fires.append({
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "brightness": float(row.get("bright_ti4", 0)),
                "frp": float(row.get("frp", 0)),  # fire radiative power, MW
                "confidence": row.get("confidence", ""),
                "acq_date": row.get("acq_date"),
            })
        except (KeyError, ValueError):
            continue
    return fires


def days_ago_iso(iso_string):
    """rough age in days of an ISO timestamp, for freshness labels"""
    try:
        t = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).days
    except Exception:
        return None
