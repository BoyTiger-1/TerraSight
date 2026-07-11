# loads trained models once and serves predictions + local explanations
import json
import os

import joblib
import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
_loaded = {}


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
        except Exception:
            _loaded[name] = None
    return _loaded[name]
