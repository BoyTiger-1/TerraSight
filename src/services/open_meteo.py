# wrappers around the Open-Meteo family of APIs, all free and keyless
# forecast/archive give real observed + modeled weather, air quality comes from
# the CAMS model, flood from GloFAS river discharge, climate from CMIP6 runs
import math

from src import config
from src.http_client import fetch_json, TTL_FORECAST, TTL_ARCHIVE, TTL_LIVE


def geocode(query, count=6):
    """search place names, returns [{name, lat, lon, country, admin1, population}]"""
    data = fetch_json(config.OPEN_METEO_GEOCODING,
                      {"name": query, "count": count, "language": "en", "format": "json"},
                      ttl=TTL_ARCHIVE)
    out = []
    for r in (data or {}).get("results", []) or []:
        out.append({
            "name": r.get("name"),
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            "country": r.get("country_code"),
            "admin1": r.get("admin1"),
            "population": r.get("population"),
            "elevation": r.get("elevation"),
        })
    return out


def forecast(lat, lon, hourly=None, daily=None, past_days=0, forecast_days=7, extra=None):
    """current + forecast weather, pass variable lists per the open-meteo docs"""
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "timezone": "auto", "forecast_days": forecast_days,
    }
    if past_days:
        params["past_days"] = past_days
    if hourly:
        params["hourly"] = ",".join(hourly)
    if daily:
        params["daily"] = ",".join(daily)
    if extra:
        params.update(extra)
    return fetch_json(config.OPEN_METEO_FORECAST, params, ttl=TTL_FORECAST)


def archive(lat, lon, start_date, end_date, daily=None, hourly=None):
    """ERA5 reanalysis back to 1940, this is real observed-assimilated data"""
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "start_date": start_date, "end_date": end_date, "timezone": "auto",
    }
    if daily:
        params["daily"] = ",".join(daily)
    if hourly:
        params["hourly"] = ",".join(hourly)
    return fetch_json(config.OPEN_METEO_ARCHIVE, params, ttl=TTL_ARCHIVE, timeout=40)


def air_quality(lat, lon, hourly=None, past_days=1, forecast_days=4):
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "timezone": "auto", "past_days": past_days, "forecast_days": forecast_days,
        "hourly": ",".join(hourly or [
            "pm2_5", "pm10", "ozone", "nitrogen_dioxide", "sulphur_dioxide",
            "carbon_monoxide", "us_aqi", "dust", "aerosol_optical_depth",
        ]),
    }
    return fetch_json(config.OPEN_METEO_AIR_QUALITY, params, ttl=TTL_FORECAST)


def flood(lat, lon, past_days=60, forecast_days=30):
    """GloFAS v4 river discharge for the nearest river cell, m3/s"""
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "daily": "river_discharge,river_discharge_max,river_discharge_median",
        "past_days": past_days, "forecast_days": forecast_days,
    }
    return fetch_json(config.OPEN_METEO_FLOOD, params, ttl=TTL_FORECAST)


def climate(lat, lon, start_date, end_date, daily, models=None):
    """CMIP6 downscaled projections out to 2050"""
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "start_date": start_date, "end_date": end_date,
        "models": ",".join(models or ["EC_Earth3P_HR", "MRI_AGCM3_2_S", "NICAM16_8S"]),
        "daily": ",".join(daily),
    }
    return fetch_json(config.OPEN_METEO_CLIMATE, params, ttl=TTL_ARCHIVE, timeout=40)


def marine(lat, lon, past_days=1, forecast_days=5):
    """wave height and sea surface temperature, only valid over water"""
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "hourly": "wave_height,sea_surface_temperature,wind_wave_height,swell_wave_height",
        "timezone": "auto", "past_days": past_days, "forecast_days": forecast_days,
    }
    return fetch_json(config.OPEN_METEO_MARINE, params, ttl=TTL_FORECAST)


def elevation(points):
    """batch elevation lookup, points is [(lat, lon), ...], returns list of meters"""
    lats = ",".join(str(round(p[0], 5)) for p in points)
    lons = ",".join(str(round(p[1], 5)) for p in points)
    data = fetch_json(config.OPEN_METEO_ELEVATION,
                      {"latitude": lats, "longitude": lons}, ttl=TTL_ARCHIVE)
    return (data or {}).get("elevation")


def terrain(lat, lon):
    """elevation plus slope/relief estimated from a 5-point cross ~500m wide"""
    # sample center + N/S/E/W offsets, 0.0045 deg is roughly 500m of latitude
    d = 0.0045
    pts = [(lat, lon), (lat + d, lon), (lat - d, lon), (lat, lon + d), (lat, lon - d)]
    elevs = elevation(pts)
    if not elevs or len(elevs) < 5 or elevs[0] is None:
        return None
    center, n, s, e, w = elevs
    # rise over run in each axis, then the slope of the steepest direction
    dz_ns = (n - s) / 1000.0
    lon_scale = max(math.cos(math.radians(lat)), 0.05)  # longitude shrinks toward poles
    dz_ew = (e - w) / (1000.0 * lon_scale)
    slope_deg = math.degrees(math.atan(math.hypot(dz_ns, dz_ew)))
    relief = max(elevs) - min(elevs)
    return {"elevation": center, "slope": round(slope_deg, 1), "relief": round(relief, 1)}
