# module registry: slug -> metadata + implementation
# kind explains what the module honestly does: "prediction" forecasts the hazard,
# "impact" models consequences of events science cannot predict, "monitoring"
# tracks live conditions and escalates them into risk
from src.modules import (wildfire, flood, drought, air_quality, heatwave,
                         agriculture, climate_trends, infrastructure, cyclone,
                         tornado, tsunami, landslide, avalanche, volcano,
                         earthquake, winter)

MODULES = {
    "wildfire": {
        "title": "Wildfire Intelligence", "kind": "prediction",
        "tagline": "Ignition and spread risk from live fire weather, fuel dryness, and terrain",
        "impl": wildfire, "simulatable": True,
    },
    "flood": {
        "title": "Flood Intelligence", "kind": "prediction",
        "tagline": "Rainfall flood probability plus GloFAS river discharge and live gauge readings",
        "impl": flood, "simulatable": True,
    },
    "drought": {
        "title": "Drought Intelligence", "kind": "prediction",
        "tagline": "Precipitation deficits, soil moisture, and evaporative demand against 30-year normals",
        "impl": drought, "simulatable": True,
    },
    "air-quality": {
        "title": "Air Quality Intelligence", "kind": "prediction",
        "tagline": "PM2.5, ozone, and US AQI nowcast and 4-day outlook from the CAMS model",
        "impl": air_quality, "simulatable": False,
    },
    "heatwave": {
        "title": "Heatwave Intelligence", "kind": "prediction",
        "tagline": "Extreme temperature detection against local 30-year percentile baselines",
        "impl": heatwave, "simulatable": True,
    },
    "winter": {
        "title": "Winter Storm Intelligence", "kind": "prediction",
        "tagline": "Snowfall, freezing-rain ice risk, dangerous wind chill, and blizzard potential",
        "impl": winter, "simulatable": True,
    },
    "agriculture": {
        "title": "Agriculture Intelligence", "kind": "prediction",
        "tagline": "Crop stress from soil moisture, growing degree days, frost, and water balance",
        "impl": agriculture, "simulatable": True,
    },
    "climate-trends": {
        "title": "Climate Trends", "kind": "monitoring",
        "tagline": "Warming trends since 1950 and CMIP6 projections to 2050 for any location",
        "impl": climate_trends, "simulatable": False,
    },
    "infrastructure": {
        "title": "Infrastructure Risk", "kind": "impact",
        "tagline": "Multi-hazard exposure rollup for power, transport, hospitals, and housing",
        "impl": infrastructure, "simulatable": False,
    },
    "cyclone": {
        "title": "Tropical Cyclone Intelligence", "kind": "prediction",
        "tagline": "Live NHC storm tracking, sea surface temperature, and wind field exposure",
        "impl": cyclone, "simulatable": True,
    },
    "tornado": {
        "title": "Tornado Intelligence", "kind": "prediction",
        "tagline": "Severe convective potential from CAPE, shear, and live NWS watches",
        "impl": tornado, "simulatable": True,
    },
    "tsunami": {
        "title": "Tsunami Intelligence", "kind": "impact",
        "tagline": "Generation potential from undersea seismicity and coastal elevation exposure",
        "impl": tsunami, "simulatable": False,
    },
    "landslide": {
        "title": "Landslide Intelligence", "kind": "prediction",
        "tagline": "Slope stability under antecedent rainfall using published intensity-duration thresholds",
        "impl": landslide, "simulatable": True,
    },
    "avalanche": {
        "title": "Avalanche Intelligence", "kind": "prediction",
        "tagline": "Snowpack loading, wind slab, and warming signals on avalanche terrain",
        "impl": avalanche, "simulatable": True,
    },
    "volcano": {
        "title": "Volcanic Activity", "kind": "impact",
        "tagline": "USGS alert levels, seismic unrest, and downstream ashfall air quality effects",
        "impl": volcano, "simulatable": False,
    },
    "earthquake": {
        "title": "Earthquake Impact", "kind": "impact",
        "tagline": "Shaking exposure, aftershock probability, and loss modeling. Not a prediction, honestly framed",
        "impl": earthquake, "simulatable": False,
    },
}


def module_meta():
    """registry without the impl objects, safe to hand to templates as JSON"""
    return {slug: {k: v for k, v in m.items() if k != "impl"}
            for slug, m in MODULES.items()}
