# one shared data-fetch context per location. every module pulls from here, so
# running all 16 modules costs about ten upstream calls instead of fifty.
# everything is lazy: nothing is fetched until a module asks for it.
#
# every feed goes through _resolve(), which falls back to the nearest point that
# actually has data and records how far away that was in self.provenance. that
# is what guarantees a location always has something to show: an exact reading
# when it exists, an honestly labelled nearby one when it does not.
from datetime import date, timedelta

from src.ml import features as F
from src.services import nearest, open_meteo, usgs, noaa, nasa


def _ensemble_mean(resp, variables):
    """average a multi-model daily response back onto plain variable names.

    the climate API returns temperature_2m_max_EC_Earth3P_HR and friends when
    more than one model is requested. a caller expecting temperature_2m_max
    finds nothing and concludes the feed has no data, so fold the members into
    their mean here and hand back the members under `_members` for the spread."""
    daily = (resp or {}).get("daily")
    if not daily:
        return resp
    for var in variables:
        if daily.get(var):
            continue
        members = [v for k, v in daily.items()
                   if k.startswith(var + "_") and isinstance(v, list)]
        if not members:
            continue
        daily[var] = [
            (sum(vals) / len(vals) if (vals := [m[i] for m in members
                                                if i < len(m) and m[i] is not None]) else None)
            for i in range(max(len(m) for m in members))
        ]
        daily.setdefault("_members", {})[var] = len(members)
    return resp


class EnvSnapshot:
    def __init__(self, lat, lon, name=None):
        self.lat = float(lat)
        self.lon = float(lon)
        self.name = name or f"{self.lat:.3f}, {self.lon:.3f}"
        self._cache = {}
        # feed key -> nearest.resolve() meta, surfaced to the UI as data_provenance
        self.provenance = {}

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def _resolve(self, key, fetch, ok=None, radii=nearest.DEFAULT_RADII, global_feed=False):
        """memoized fetch with nearest-point fallback, recording provenance"""
        def load():
            payload, meta = nearest.resolve(self.lat, self.lon, fetch, ok=ok,
                                            radii=radii, label=key, global_feed=global_feed)
            self.provenance[key] = meta
            return payload
        return self._memo(key, load)

    # --- weather ---

    def forecast_raw(self):
        """92 days of history + 7 day forecast, daily vars + hourly RH"""
        def fetch(lat, lon):
            return open_meteo.forecast(lat, lon, daily=F.DAILY_VARS, hourly=F.HOURLY_VARS,
                                       past_days=92, forecast_days=7)

        def ok(resp):
            # a response with a daily block but no usable temperatures is as bad
            # as no response at all, so treat it as a miss and keep searching
            d = (resp or {}).get("daily") or {}
            vals = [v for v in d.get("temperature_2m_max", []) if v is not None]
            return len(vals) >= 30

        return self._resolve("forecast", fetch, ok=ok)

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
        def fetch(lat, lon):
            return open_meteo.forecast(
                lat, lon,
                hourly=["temperature_2m", "apparent_temperature", "relative_humidity_2m",
                        "precipitation", "rain", "snowfall", "wind_speed_10m", "wind_gusts_10m",
                        "cape", "snow_depth", "soil_moisture_0_to_1cm", "soil_moisture_3_to_9cm",
                        "freezing_level_height", "wind_speed_850hPa", "wind_speed_500hPa"],
                forecast_days=3, extra={"models": "best_match"})

        def ok(resp):
            h = (resp or {}).get("hourly") or {}
            return bool([v for v in h.get("temperature_2m", []) if v is not None])

        return self._resolve("hourly", fetch, ok=ok)

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

        def fetch(_lat, _lon):
            # ERA5 is a global 0.25 deg product, so the only reason this misses is
            # the API being busy. resolve() retries the point rather than ringing
            # around it: fifteen years of daily data is the most expensive call in
            # the system and a ring would spend thirty of them for nothing.
            clat, clon = self._coarse()
            return open_meteo.archive(clat, clon, start.isoformat(), end.isoformat(),
                                      daily=["temperature_2m_max", "temperature_2m_min",
                                             "precipitation_sum"])

        def ok(resp):
            d = (resp or {}).get("daily") or {}
            return len([v for v in d.get("temperature_2m_max", []) if v is not None]) > 1000

        return self._resolve("climatology", fetch, ok=ok, global_feed=True)

    def climate_projection(self):
        """CMIP6 daily tmax/precip 2025-2050, gridded and cached like climatology.

        we ask for three downscaled models, and the climate API answers with one
        series per model per variable, suffixed with the model name. collapse
        those into an ensemble mean under the plain variable name so callers see
        the same shape as every other daily feed, and keep the members alongside
        it for anyone who wants the spread."""
        def fetch(_lat, _lon):
            clat, clon = self._coarse()
            resp = open_meteo.climate(clat, clon, "2025-01-01", "2050-12-31",
                                      daily=["temperature_2m_max", "precipitation_sum"])
            return _ensemble_mean(resp, ("temperature_2m_max", "precipitation_sum"))

        def ok(resp):
            d = (resp or {}).get("daily") or {}
            return len([v for v in d.get("temperature_2m_max", []) if v is not None]) > 500

        return self._resolve("projection", fetch, ok=ok, global_feed=True)

    # --- hazard-specific feeds ---

    def air(self):
        def ok(resp):
            h = (resp or {}).get("hourly") or {}
            return bool([v for v in h.get("pm2_5", []) if v is not None])
        return self._resolve("air_quality", open_meteo.air_quality, ok=ok)

    def flood(self):
        """GloFAS only models cells that contain a river. small catchments and
        arid interiors legitimately have none, so search a wide radius for the
        nearest modelled reach rather than reporting nothing."""
        def ok(resp):
            d = (resp or {}).get("daily") or {}
            return bool([v for v in d.get("river_discharge", []) if v is not None])
        return self._resolve("river_discharge", open_meteo.flood, ok=ok,
                             radii=(25, 60, 120, 250, 400))

    def marine(self):
        """wave height and SST exist over water only. inland points get the
        nearest coastal cell, which is exactly what cyclone and tsunami exposure
        should be reading anyway."""
        def ok(resp):
            h = (resp or {}).get("hourly") or {}
            return bool([v for v in h.get("sea_surface_temperature", []) if v is not None])
        return self._resolve("marine", open_meteo.marine, ok=ok, radii=nearest.WIDE_RADII)

    def terrain(self):
        return self._resolve("terrain", open_meteo.terrain,
                             ok=lambda t: bool(t) and t.get("elevation") is not None,
                             radii=(10, 25, 60))

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
        """USGS stream gauges, CONUS only. the service returns sites inside a
        bounding box, so widen the box rather than moving the point."""
        if not (24 <= self.lat <= 50 and -125 <= self.lon <= -66):
            return []

        def load():
            # NWIS rejects a bounding box larger than 25 square degrees, and
            # box_deg is a half-width, so anything past 2.5 comes back empty no
            # matter how many gauges are inside it. 2.5 is the widest net the
            # service will actually cast.
            for box_deg in (0.75, 1.5, 2.5):
                sites = usgs.river_gauges(self.lat, self.lon, box_deg=box_deg) or []
                if sites:
                    nearest_km = min((self.distance_to(s["lat"], s["lon"])
                                      for s in sites if s.get("lat") is not None), default=None)
                    self.provenance["river_gauges"] = {
                        "feed": "river_gauges",
                        "status": "exact" if box_deg == 0.75 else "nearest",
                        "distance_km": round(nearest_km, 1) if nearest_km else None,
                        "source": [self.lat, self.lon], "requested": [self.lat, self.lon]}
                    return sites
            self.provenance["river_gauges"] = {"feed": "river_gauges", "status": "unavailable",
                                               "distance_km": None}
            return []
        return self._memo("gauges", load)

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

    # --- reporting ---

    def coverage(self):
        """what the UI shows in the data-provenance chip: only the feeds that
        did not answer at the exact point are interesting."""
        rows = [dict(m) for m in self.provenance.values() if m.get("status") != "exact"]
        rows.sort(key=lambda m: -(m.get("distance_km") or 0))
        exact = sum(1 for m in self.provenance.values() if m.get("status") == "exact")
        return {"feeds_exact": exact, "feeds_total": len(self.provenance),
                "substituted": rows}
