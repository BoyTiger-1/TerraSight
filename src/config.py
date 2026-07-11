# central place for API endpoints, keys, and shared constants
import os

# every endpoint here is free and keyless unless noted otherwise
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_AIR_QUALITY = "https://air-quality-api.open-meteo.com/v1/air-quality"
OPEN_METEO_FLOOD = "https://flood-api.open-meteo.com/v1/flood"
OPEN_METEO_CLIMATE = "https://climate-api.open-meteo.com/v1/climate"
OPEN_METEO_MARINE = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_ELEVATION = "https://api.open-meteo.com/v1/elevation"
OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"

USGS_EARTHQUAKES = "https://earthquake.usgs.gov/fdsnws/event/1/query"
USGS_WATER = "https://waterservices.usgs.gov/nwis/iv/"
# HANS is the USGS volcano notification system, returns current alert levels
USGS_VOLCANOES = "https://volcanoes.usgs.gov/hans-public/api/volcano/getMonitoredVolcanoes"

NWS_ALERTS = "https://api.weather.gov/alerts/active"
NHC_STORMS = "https://www.nhc.noaa.gov/CurrentStorms.json"
EONET_EVENTS = "https://eonet.gsfc.nasa.gov/api/v3/events"

# FIRMS needs a free MAP_KEY, everything still works without it
FIRMS_MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "")
FIRMS_AREA = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# api.weather.gov rejects requests without a descriptive user agent
USER_AGENT = "TerraSight-Climate-Intelligence (educational; ronitcodes@gmail.com)"

# shared risk band definitions, score is always 0-100
RISK_BANDS = [
    (0, 25, "Low", "#4cb782"),
    (25, 50, "Moderate", "#d9a13b"),
    (50, 75, "High", "#e0703a"),
    (75, 101, "Extreme", "#e05252"),
]


def risk_band(score):
    """map a 0-100 score onto its label and color"""
    for lo, hi, label, color in RISK_BANDS:
        if lo <= score < hi:
            return label, color
    return "Extreme", "#e05252"
