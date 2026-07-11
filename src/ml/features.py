# feature engineering shared by training (ERA5 archive) and inference (forecast API)
# both APIs return the same daily/hourly variable names, so one code path serves both
import math


def daily_frame(resp):
    """flatten an open-meteo response into aligned per-day arrays.
    expects daily tmax/tmin/precip/wind/gusts/et0/snowfall and hourly RH."""
    if not resp or "daily" not in resp:
        return None
    d = resp["daily"]
    frame = {
        "time": d.get("time", []),
        "tmax": d.get("temperature_2m_max", []),
        "tmin": d.get("temperature_2m_min", []),
        "precip": d.get("precipitation_sum", []),
        "wind_max": d.get("windspeed_10m_max", []) or d.get("wind_speed_10m_max", []),
        "gust_max": d.get("windgusts_10m_max", []) or d.get("wind_gusts_10m_max", []),
        "et0": d.get("et0_fao_evapotranspiration", []),
        "snowfall": d.get("snowfall_sum", []),
    }
    # collapse hourly humidity down to a daily minimum, 24 readings per day
    rh_hourly = (resp.get("hourly") or {}).get("relative_humidity_2m", [])
    rh_min = []
    for i in range(len(frame["time"])):
        day = [v for v in rh_hourly[i * 24:(i + 1) * 24] if v is not None]
        rh_min.append(min(day) if day else None)
    frame["rh_min"] = rh_min
    return frame


def _sum(vals):
    return sum(v for v in vals if v is not None)


def _clean(v, fallback=0.0):
    return fallback if v is None else v


def vapor_pressure_deficit(t_c, rh):
    """kPa, how hard the air pulls moisture out of fuels (Tetens formula)"""
    es = 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))
    return es * (1 - rh / 100.0)


def wildfire_features(frame, i):
    """fire-weather feature vector for day i, needs >= 90 days of history before i"""
    if i < 90 or i >= len(frame["time"]):
        return None
    tmax = _clean(frame["tmax"][i], 20)
    rh = _clean(frame["rh_min"][i], 40)
    precip_7 = _sum(frame["precip"][i - 6:i + 1])
    precip_30 = _sum(frame["precip"][i - 29:i + 1])
    precip_90 = _sum(frame["precip"][i - 89:i + 1])
    et0_7 = _sum(frame["et0"][i - 6:i + 1])
    et0_30 = _sum(frame["et0"][i - 29:i + 1])
    # walk backwards until we hit a wetting rain day (>= 2.5mm)
    days_since_rain = 0
    for j in range(i, max(i - 90, -1), -1):
        if _clean(frame["precip"][j]) >= 2.5:
            break
        days_since_rain += 1
    tmax_7 = [v for v in frame["tmax"][i - 6:i + 1] if v is not None]
    return {
        "tmax_c": round(tmax, 2),
        "rh_min_pct": round(rh, 1),
        "wind_max_kmh": round(_clean(frame["wind_max"][i]), 1),
        "gust_max_kmh": round(_clean(frame["gust_max"][i]), 1),
        "precip_7d_mm": round(precip_7, 1),
        "precip_30d_mm": round(precip_30, 1),
        "precip_90d_mm": round(precip_90, 1),
        "days_since_rain": days_since_rain,
        "et0_7d_mm": round(et0_7, 1),
        "vpd_kpa": round(vapor_pressure_deficit(tmax, rh), 3),
        # fuel dryness: how much the atmosphere demanded vs what rain supplied
        "dryness_ratio": round(et0_30 / (precip_30 + 10.0), 3),
        "tmax_7d_mean": round(sum(tmax_7) / len(tmax_7), 2) if tmax_7 else tmax,
    }


def flood_features(frame, i):
    """rainfall-flood feature vector for day i, needs >= 30 days of history"""
    if i < 30 or i >= len(frame["time"]):
        return None
    # antecedent precipitation index, yesterday counts more than last week
    api = 0.0
    for k in range(30):
        api += _clean(frame["precip"][i - k]) * (0.9 ** k)
    window30 = [_clean(v) for v in frame["precip"][i - 29:i + 1]]
    return {
        "precip_1d_mm": round(_clean(frame["precip"][i]), 1),
        "precip_3d_mm": round(_sum(frame["precip"][i - 2:i + 1]), 1),
        "precip_7d_mm": round(_sum(frame["precip"][i - 6:i + 1]), 1),
        "precip_30d_mm": round(_sum(frame["precip"][i - 29:i + 1]), 1),
        "precip_max1d_30d_mm": round(max(window30), 1),
        "api_index": round(api, 1),
        "wet_days_30d": sum(1 for v in window30 if v >= 5.0),
        "tmax_c": round(_clean(frame["tmax"][i], 20), 2),
        "tmin_c": round(_clean(frame["tmin"][i], 10), 2),
        "snowfall_30d_cm": round(_sum(frame["snowfall"][i - 29:i + 1]), 1),
        "et0_30d_mm": round(_sum(frame["et0"][i - 29:i + 1]), 1),
    }


# the daily/hourly variable lists every fetch uses, keep in one place
DAILY_VARS = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum",
              "windspeed_10m_max", "windgusts_10m_max",
              "et0_fao_evapotranspiration", "snowfall_sum"]
HOURLY_VARS = ["relative_humidity_2m"]
