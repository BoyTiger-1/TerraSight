# one shared data-fetch context per location. every module pulls from here, so
# running all 15 modules costs about ten upstream calls instead of fifty.
# everything is lazy: nothing is fetched until a module asks for it.
from datetime import date, timedelta

from src.ml import features as F
from src.services import open_meteo, usgs, noaa, nasa


class EnvSnapshot:
    def __init__(self, lat, lon, name=None):
        self.lat = float(lat)
        self.lon = float(lon)
        self.name = name or f"{self.lat:.3f}, {self.lon:.3f}"
        self._cache = {}

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # --- weather ---

    def forecast_raw(self):
        """92 days of history + 7 day forecast, daily vars + hourly RH"""
        return self._memo("forecast_raw", lambda: open_meteo.forecast(
            self.lat, self.lon, daily=F.DAILY_VARS, hourly=F.HOURLY_VARS,
            past_days=92, forecast_days=7))

    def daily(self):
        """the flattened frame shared with the ML feature code"""
        return self._memo("daily", lambda: F.daily_frame(self.forecast_raw()))

    def today_index(self):
        frame = self.daily()
        if not frame:
            return None
        today = date.today().isoformat()
        try:
            return frame["time"].index(today)
        except ValueError:
            return len(frame["time"]) - 8  # fall back to the last historical day

    def hourly(self):
        """48h of hourly detail for convective, heat, and winter modules.
        pressure-level winds give a real deep-layer shear proxy for tornadoes."""
        return self._memo("hourly", lambda: open_meteo.forecast(
            self.lat, self.lon,
            hourly=["temperature_2m", "apparent_temperature", "relative_humidity_2m",
                    "precipitation", "rain", "snowfall", "wind_speed_10m", "wind_gusts_10m",
                    "cape", "snow_depth", "soil_moisture_0_to_1cm", "soil_moisture_3_to_9cm",
                    "freezing_level_height", "wind_speed_850hPa", "wind_speed_500hPa"],
            forecast_days=3, extra={"models": "best_match"}))

    def _coarse(self):
        """snap to a 0.25 degree (~28 km) grid so a whole metro area reuses one
        cached climatology call instead of hammering the archive per click.
        climate normals barely change over that distance."""
        return round(self.lat * 4) / 4, round(self.lon * 4) / 4

    def climatology(self):
        """15 years of daily tmax/tmin/precip, the baseline for anomalies.
        the archive API is rate-limited and this is its most expensive call, so
        keep the window modest, snap to a grid, and cache for a day."""
        end = date.today() - timedelta(days=7)
        start = end.replace(year=end.year - 15)
        clat, clon = self._coarse()
        return self._memo("climatology", lambda: open_meteo.archive(
            clat, clon, start.isoformat(), end.isoformat(),
            daily=["temperature_2m_max", "temperature_2m_min", "precipitation_sum"]))

    def climate_projection(self):
        """CMIP6 daily tmax/precip 2025-2050, gridded and cached like climatology"""
        clat, clon = self._coarse()
        return self._memo("projection", lambda: open_meteo.climate(
            clat, clon, "2025-01-01", "2050-12-31",
            daily=["temperature_2m_max", "precipitation_sum"]))

    # --- hazard-specific feeds ---

    def air(self):
        return self._memo("air", lambda: open_meteo.air_quality(self.lat, self.lon))

    def flood(self):
        return self._memo("flood", lambda: open_meteo.flood(self.lat, self.lon))

    def marine(self):
        return self._memo("marine", lambda: open_meteo.marine(self.lat, self.lon))

    def terrain(self):
        return self._memo("terrain", lambda: open_meteo.terrain(self.lat, self.lon))

    def alerts(self):
        # NWS only covers the US, elsewhere this quietly returns []
        if not (18 <= self.lat <= 72 and -180 <= self.lon <= -60):
            return []
        return self._memo("alerts", lambda: noaa.active_alerts(self.lat, self.lon) or [])

    def quakes(self, radius_km=300, days=30, min_mag=2.5):
        key = f"quakes_{radius_km}_{days}_{min_mag}"
        return self._memo(key, lambda: usgs.earthquakes(
            self.lat, self.lon, radius_km=radius_km, days=days, min_magnitude=min_mag))

    def gauges(self):
        if not (24 <= self.lat <= 50 and -125 <= self.lon <= -66):
            return []
        return self._memo("gauges", lambda: usgs.river_gauges(self.lat, self.lon) or [])

    def volcanoes(self):
        return self._memo("volcanoes", lambda: usgs.volcanoes() or [])

    def storms(self):
        return self._memo("storms", lambda: noaa.active_storms() or [])

    def eonet(self, category=None):
        key = f"eonet_{category}"
        return self._memo(key, lambda: nasa.eonet_events(category=category) or [])

    def fires_nearby(self):
        """FIRMS detections if a key is set, otherwise EONET wildfire events"""
        def load():
            fires = nasa.firms_fires(self.lat, self.lon)
            if fires is not None:
                return {"source": "NASA FIRMS (VIIRS)", "fires": fires}
            events = [e for e in self.eonet("wildfires")
                      if e["lat"] is not None and
                      noaa.haversine_km(self.lat, self.lon, e["lat"], e["lon"]) < 300]
            return {"source": "NASA EONET", "fires": events}
        return self._memo("fires", load)

    def distance_to(self, lat, lon):
        return noaa.haversine_km(self.lat, self.lon, lat, lon)
