# "every location must return something" resolver.
#
# some feeds genuinely do not exist at an arbitrary point: GloFAS has no river
# cell in the middle of a plateau, the marine model has no sea surface temp 800
# km inland, the elevation service occasionally drops a tile. rather than
# reporting "no data available" we walk outward from the requested point and
# take the first place that answers, then tell the user exactly how far away
# that was. a nearest-neighbour reading with an honest distance label beats a
# blank panel every time.
import math
import time

EARTH_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def offset(lat, lon, bearing_deg, distance_km):
    """move distance_km from (lat, lon) along a compass bearing, flat-earth
    approximation. good to a fraction of a percent at these ranges."""
    dlat = distance_km / 111.32
    lon_scale = max(math.cos(math.radians(lat)), 0.08)  # longitude shrinks toward the poles
    dlon = distance_km / (111.32 * lon_scale)
    b = math.radians(bearing_deg)
    nlat = max(-89.9, min(89.9, lat + dlat * math.cos(b)))
    nlon = lon + dlon * math.sin(b)
    # wrap the antimeridian instead of producing an out-of-range longitude
    while nlon > 180:
        nlon -= 360
    while nlon < -180:
        nlon += 360
    return round(nlat, 4), round(nlon, 4)


def ring(lat, lon, radius_km, count=8):
    """candidate points evenly spaced around a circle, starting due north"""
    return [offset(lat, lon, i * (360.0 / count), radius_km) for i in range(count)]


# search radii in km. the early rings are cheap and catch grid-edge misses; the
# far rings only matter for genuinely absent feeds like marine data inland.
DEFAULT_RADII = (25, 60, 120, 250)
WIDE_RADII = (40, 100, 200, 400, 700, 1100)


# how long to wait between retries of a global feed, in seconds. short enough
# that a page load still finishes, long enough to outlast a momentary blip.
GLOBAL_RETRY_DELAYS = (1.5, 4.0)


def resolve(lat, lon, fetch, ok=None, radii=DEFAULT_RADII, ring_points=8, label="",
            global_feed=False):
    """try fetch(lat, lon) at the exact point, then on widening rings.

    fetch: callable(lat, lon) -> payload or None
    ok:    callable(payload) -> bool, defaults to "not falsy"

    global_feed marks a product that covers the whole planet, like the ERA5
    reanalysis or the CMIP6 projections. those have no coverage gaps, so a miss
    is always the upstream being busy, never geography. walking a ring would ask
    the same overloaded service the same expensive question up to thirty more
    times and still deserve the same answer, so retry the point itself instead
    and say "throttled", which is what actually happened.

    returns (payload, meta). meta always carries a status the UI can show:
      exact       the requested point answered
      nearest     a nearby point answered, distance_km says how far
      throttled   a global feed did not answer in time; try again shortly
      unavailable nothing answered inside the search radius
    """
    ok = ok or (lambda v: bool(v))
    meta = {"feed": label, "requested": [round(lat, 4), round(lon, 4)],
            "source": [round(lat, 4), round(lon, 4)], "distance_km": 0.0,
            "status": "exact"}

    payload = fetch(lat, lon)
    if ok(payload):
        return payload, meta

    if global_feed:
        for delay in GLOBAL_RETRY_DELAYS:
            time.sleep(delay)
            payload = fetch(lat, lon)
            if ok(payload):
                return payload, meta
        meta["status"] = "throttled"
        return None, meta

    for radius in radii:
        # a coarse ring first, refined only if the whole ring misses, so a point
        # one grid cell off the coast costs one extra call and not thirty
        for clat, clon in ring(lat, lon, radius, ring_points):
            payload = fetch(clat, clon)
            if ok(payload):
                meta.update({"source": [clat, clon], "status": "nearest",
                             "distance_km": round(haversine_km(lat, lon, clat, clon), 1)})
                return payload, meta

    meta["status"] = "unavailable"
    return None, meta


def describe(meta):
    """one human sentence for the provenance chip in the UI"""
    if not meta:
        return ""
    if meta["status"] == "exact":
        return "measured at this location"
    if meta["status"] == "nearest":
        return f"nearest available reading, {meta['distance_km']:.0f} km away"
    if meta["status"] == "throttled":
        return "the archive was busy, this refreshes on the next load"
    return "no coverage found within the search radius"
