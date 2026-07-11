# order-of-magnitude economic impact estimates, in the spirit of FEMA Hazus:
# exposed population x built value per person x a damage ratio that grows with
# hazard severity. these are planning numbers, not insurance quotes, and the
# UI labels them that way.
from src.data.cities import population_exposure, nearby_cities

# national average built-environment replacement value per resident (structures
# plus contents), a round approximation of Hazus building stock data
EXPOSURE_PER_CAPITA = 120_000

# fraction of exposed value actually lost at a given severity, per hazard.
# wildfires destroy what they touch but touch a narrow footprint, floods damage
# wide areas partially, quake losses scale with shaking
DAMAGE_PROFILES = {
    "wildfire": {"footprint": 0.06, "max_ratio": 0.60},
    "flood": {"footprint": 0.20, "max_ratio": 0.35},
    "cyclone": {"footprint": 0.55, "max_ratio": 0.30},
    "tornado": {"footprint": 0.015, "max_ratio": 0.80},
    "earthquake": {"footprint": 0.70, "max_ratio": 0.45},
    "tsunami": {"footprint": 0.10, "max_ratio": 0.70},
    "landslide": {"footprint": 0.01, "max_ratio": 0.90},
    "avalanche": {"footprint": 0.004, "max_ratio": 0.90},
    "volcano": {"footprint": 0.15, "max_ratio": 0.40},
    "heatwave": {"footprint": 0.90, "max_ratio": 0.02},   # productivity + health costs
    "drought": {"footprint": 0.90, "max_ratio": 0.04},    # mostly agricultural
    "default": {"footprint": 0.10, "max_ratio": 0.30},
}


def _fmt_money(x):
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.0f}M"
    return f"${x / 1e3:.0f}K"


def estimate(hazard, lat, lon, score, radius_km=50):
    """impact block for a module result. score is the module's 0-100 risk."""
    profile = DAMAGE_PROFILES.get(hazard, DAMAGE_PROFILES["default"])
    exposed = population_exposure(lat, lon, radius_km)
    severity = score / 100.0
    # damage ratio ramps quadratically, low risk means near-zero expected loss
    ratio = profile["max_ratio"] * severity ** 2
    expected_loss = exposed * EXPOSURE_PER_CAPITA * profile["footprint"] * ratio
    cities = nearby_cities(lat, lon, radius_km)[:5]
    return {
        "radius_km": radius_km,
        "population_exposed": exposed,
        "expected_loss_usd": int(expected_loss),
        "expected_loss_label": _fmt_money(expected_loss),
        "damage_ratio_pct": round(ratio * 100, 1),
        "nearby_cities": cities,
        "note": ("Planning estimate based on population exposure, average built "
                 "value per resident, and a severity-scaled damage ratio. "
                 "Methodology inspired by FEMA Hazus, accurate to order of magnitude."),
    }
