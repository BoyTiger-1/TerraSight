# NOAA feeds: NWS active alerts and the National Hurricane Center storm list
import math

from src import config
from src.http_client import fetch_json, TTL_LIVE


def active_alerts(lat=None, lon=None):
    """live watches/warnings/advisories from api.weather.gov, US only"""
    params = {"status": "actual", "message_type": "alert,update"}
    if lat is not None:
        params["point"] = f"{round(lat, 4)},{round(lon, 4)}"
    data = fetch_json(config.NWS_ALERTS, params, ttl=TTL_LIVE, timeout=25)
    alerts = []
    for f in (data or {}).get("features", []):
        p = f.get("properties", {})
        alerts.append({
            "event": p.get("event"), "severity": p.get("severity"),
            "urgency": p.get("urgency"), "certainty": p.get("certainty"),
            "headline": p.get("headline"), "area": p.get("areaDesc"),
            "onset": p.get("onset"), "ends": p.get("ends") or p.get("expires"),
            "instruction": p.get("instruction"), "description": p.get("description"),
            "id": p.get("id"),
        })
    return alerts


def alerts_matching(alerts, keywords):
    """filter a list of alerts down to events containing any keyword"""
    hits = []
    for a in alerts:
        ev = (a.get("event") or "").lower()
        if any(k in ev for k in keywords):
            hits.append(a)
    return hits


def active_storms():
    """tropical cyclones the NHC is currently tracking in the Atlantic + East Pacific"""
    data = fetch_json(config.NHC_STORMS, ttl=TTL_LIVE, timeout=25)
    storms = []
    for s in (data or {}).get("activeStorms", []):
        storms.append({
            "id": s.get("id"), "name": s.get("name"),
            "classification": s.get("classification"),
            "intensity_kt": s.get("intensity"),  # sustained wind in knots
            "pressure_mb": s.get("pressure"),
            "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"),
            "movement_dir": s.get("movementDir"), "movement_speed_kt": s.get("movementSpeed"),
            "last_update": s.get("lastUpdate"), "basin": s.get("binNumber"),
        })
    return storms


def haversine_km(lat1, lon1, lat2, lon2):
    """great-circle distance, used all over for proximity checks"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
