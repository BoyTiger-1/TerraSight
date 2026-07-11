# legacy wildfire prediction interface, kept so the original API contract
# (/api/wildfire/predict and /predict-manual) still works for old clients.
# under the hood it now runs the real-data model, no synthetic anything.
from datetime import datetime

from src.ml import features as F
from src.ml.registry import get_model
from src.services import open_meteo


class WildfirePredictionSystem:
    def __init__(self):
        self.model = None

    def initialize_model(self):
        """load the gradient boosting model trained on real historical fires"""
        self.model = get_model("wildfire")
        return self.model is not None

    def _risk_bucket(self, p):
        if p < 0.3:
            return "Low", "#4cb782"
        if p < 0.6:
            return "Moderate", "#d9a13b"
        if p < 0.8:
            return "High", "#e0703a"
        return "Extreme", "#e05252"

    def predict_wildfire_risk(self, lat, lon, manual_data=None, weather_api_key=None):
        """same response shape the original app returned, now on real data.
        weather_api_key is accepted for backward compatibility and ignored,
        the platform no longer needs paid keys."""
        if self.model is None:
            raise ValueError("Model not trained. Please load the model first.")

        if manual_data:
            # translate the old manual fields onto the new feature names,
            # anything not provided falls back to the training median
            feats = {
                "tmax_c": manual_data.get("temperature"),
                "tmax_7d_mean": manual_data.get("temperature"),
                "rh_min_pct": manual_data.get("humidity"),
                "wind_max_kmh": manual_data.get("wind_speed"),
                "gust_max_kmh": (manual_data.get("wind_speed") or 0) * 1.5,
                "precip_7d_mm": (manual_data.get("precipitation") or 0) * 7,
                "precip_30d_mm": (manual_data.get("precipitation") or 0) * 30,
            }
            t, rh = manual_data.get("temperature"), manual_data.get("humidity")
            if t is not None and rh is not None:
                feats["vpd_kpa"] = round(F.vapor_pressure_deficit(t, rh), 3)
            feats = {k: v for k, v in feats.items() if v is not None}
            used_features = {**manual_data}
        else:
            # live fetch: 92 days of history + today from the forecast API
            resp = open_meteo.forecast(lat, lon, daily=F.DAILY_VARS,
                                       hourly=F.HOURLY_VARS, past_days=92, forecast_days=1)
            frame = F.daily_frame(resp)
            if not frame:
                return {"error": "Failed to fetch required environmental data"}
            feats = F.wildfire_features(frame, len(frame["time"]) - 1)
            if not feats:
                return {"error": "Not enough weather history at this location"}
            used_features = feats

        prob = self.model.predict(feats)
        level, color = self._risk_bucket(prob)
        return {
            "location": {"latitude": lat, "longitude": lon},
            "prediction": {
                "fire_risk_probability": float(prob),
                "fire_predicted": bool(prob >= 0.5),
                "risk_level": level,
                "risk_color": color,
            },
            "input_features": used_features,
            "timestamp": datetime.now().isoformat(),
            "model": "gradient boosting trained on 80 documented US wildfires + ERA5",
        }
