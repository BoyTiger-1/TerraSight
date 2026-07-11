# location search that understands both city names and full street addresses.
# open-meteo is fast and gives populations for cities; nominatim (openstreetmap)
# covers real addresses and reverse lookups when you click the map.
from src import config
from src.http_client import fetch_json, TTL_ARCHIVE
from src.services import open_meteo


def _looks_like_address(q):
    """a house number or a comma usually means it is an address, not a city"""
    return any(c.isdigit() for c in q) or q.count(",") >= 1


def nominatim_search(query, limit=5):
    """forward geocode an address or place through OSM Nominatim"""
    data = fetch_json(config.NOMINATIM_SEARCH,
                      {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": limit},
                      ttl=TTL_ARCHIVE, timeout=15)
    out = []
    for r in data or []:
        addr = r.get("address", {})
        # pick the most specific populated-place field available for the label
        city = (addr.get("city") or addr.get("town") or addr.get("village")
                or addr.get("hamlet") or addr.get("suburb") or r.get("name"))
        out.append({
            "name": city or (r.get("display_name", "").split(",")[0]),
            "lat": float(r["lat"]), "lon": float(r["lon"]),
            "admin1": addr.get("state"), "country": (addr.get("country_code") or "").upper(),
            "population": None,
            "label": r.get("display_name"),
        })
    return out


def reverse(lat, lon):
    """turn a clicked map point into a readable place name"""
    data = fetch_json(config.NOMINATIM_REVERSE,
                      {"lat": round(lat, 5), "lon": round(lon, 5),
                       "format": "jsonv2", "zoom": 10, "addressdetails": 1},
                      ttl=TTL_ARCHIVE, timeout=15)
    if not data:
        return None
    addr = data.get("address", {})
    name = (addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("county") or addr.get("state") or data.get("display_name", ""))
    state = addr.get("state")
    return {"name": name, "admin1": state, "country": (addr.get("country_code") or "").upper(),
            "lat": lat, "lon": lon}


def search(query, count=6):
    """merged search: open-meteo cities first, then nominatim for addresses.
    dedupes points that land within ~1 km of each other."""
    query = query.strip()
    results = []

    # cities from open-meteo unless the query is clearly a street address
    if not _looks_like_address(query):
        results = open_meteo.geocode(query, count=count) or []

    # add address matches when it looks like one, or when cities came up short
    if _looks_like_address(query) or len(results) < 2:
        for r in nominatim_search(query, limit=count):
            dup = any(abs(r["lat"] - e["lat"]) < 0.01 and abs(r["lon"] - e["lon"]) < 0.01
                      for e in results)
            if not dup:
                results.append(r)

    return results[:count]
