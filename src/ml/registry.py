# loads trained models once and serves predictions + local explanations
import json
import os

import joblib
import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
_loaded = {}

# why a model failed to load. a silently missing model reports itself downstream
# as "weather data unavailable", which sends you looking at the API feeds for a
# problem that is actually in the pickle, so keep the real exception.
_load_errors = {}


def load_status():
    """per-model load state for /api/health"""
    out = {}
    for name in ("wildfire", "flood"):
        get_model(name)
        out[name] = {"loaded": _loaded.get(name) is not None,
                     "error": _load_errors.get(name)}
    try:
        import sklearn
        out["sklearn"] = sklearn.__version__
    except Exception:
        out["sklearn"] = None
    return out


class HazardModel:
    def __init__(self, name):
        bundle = joblib.load(os.path.join(MODEL_DIR, f"{name}.pkl"))
        self.name = name
        self.model = bundle["model"]
        self.features = bundle["features"]
        self.medians = bundle["medians"]
        card_path = os.path.join(MODEL_DIR, f"{name}_card.json")
        self.card = json.load(open(card_path)) if os.path.exists(card_path) else {}

    def _vector(self, feats):
        # missing keys fall back to the training median so one bad API field
        # never crashes a prediction
        return np.array([[feats.get(k, self.medians[k]) for k in self.features]])

    def predict(self, feats):
        """probability that conditions match historical disaster days"""
        return float(self.model.predict_proba(self._vector(feats))[0][1])

    def explain(self, feats):
        """occlusion explanation: swap each feature for its training median and
        see how much the probability moves. positive delta = pushing risk up."""
        base = self.predict(feats)
        rows = []
        for k in self.features:
            neutral = dict(feats)
            neutral[k] = self.medians[k]
            delta = base - self.predict(neutral)
            rows.append({"feature": k, "value": feats.get(k), "delta": round(delta, 4)})
        rows.sort(key=lambda r: -abs(r["delta"]))
        return base, rows


def get_model(name):
    """cached loader, returns None if the pkl has not been trained yet"""
    if name not in _loaded:
        try:
            _loaded[name] = HazardModel(name)
        except Exception as e:
            _loaded[name] = None
            _load_errors[name] = f"{type(e).__name__}: {e}"[:300]
    return _loaded[name]
