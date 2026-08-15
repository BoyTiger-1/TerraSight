# trains the wildfire and flood models on real historical events + ERA5 weather
# run with: python -m src.ml.train
# positives = documented disasters, negatives = the same places in calm seasons
# and other years, plus random CONUS locations on random dates. this is the
# standard presence / pseudo-absence design used in hazard modeling papers.
import json
import os
import random
import sys
from datetime import date, timedelta

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.data.fire_history import FIRE_EVENTS
from src.data.flood_history import FLOOD_EVENTS
from src.ml import features as F
from src.services import open_meteo

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
rng = random.Random(42)  # deterministic sampling so retrains are reproducible

# physics priors baked into the trees: +1 means risk can only rise with the
# feature, -1 only fall, 0 unconstrained. keeps a small dataset from learning
# nonsense like "more wind, less fire"
MONOTONE = {
    "wildfire": {
        "tmax_c": 1, "rh_min_pct": -1, "wind_max_kmh": 1, "gust_max_kmh": 1,
        "precip_7d_mm": -1, "precip_30d_mm": -1, "precip_90d_mm": -1,
        "days_since_rain": 1, "et0_7d_mm": 1, "vpd_kpa": 1,
        "dryness_ratio": 1, "tmax_7d_mean": 1,
    },
    "flood": {
        "precip_1d_mm": 1, "precip_3d_mm": 1, "precip_7d_mm": 1,
        "precip_30d_mm": 1, "precip_max1d_30d_mm": 1, "api_index": 1,
        "wet_days_30d": 1, "tmax_c": 0, "tmin_c": 0,
        "snowfall_30d_cm": 1, "et0_30d_mm": -1,
    },
}


def fetch_frame(lat, lon, target_date):
    """ERA5 daily history for the 130 days ending 3 days after the event"""
    d = date.fromisoformat(target_date)
    start = (d - timedelta(days=127)).isoformat()
    end = min(d + timedelta(days=3), date.today() - timedelta(days=6)).isoformat()
    resp = open_meteo.archive(lat, lon, start, end, daily=F.DAILY_VARS, hourly=F.HOURLY_VARS)
    frame = F.daily_frame(resp)
    if not frame:
        return None, None
    try:
        idx = frame["time"].index(target_date)
    except ValueError:
        return None, None
    return frame, idx


def collect(events, feature_fn, label, samples):
    """turn a list of (name, date, lat, lon) into labeled feature rows"""
    for name, day, lat, lon in events:
        frame, idx = fetch_frame(lat, lon, day)
        if frame is None:
            print(f"  skip (no data): {name}")
            continue
        feats = feature_fn(frame, idx)
        if feats:
            samples.append((feats, label))
            print(f"  {'FIRE' if label else 'calm'} {name} {day} ok")


def negatives_for(events, feature_fn, samples):
    """counter-examples: same spot 5 months earlier, and 2 years earlier"""
    for name, day, lat, lon in events:
        d = date.fromisoformat(day)
        for shifted in [d - timedelta(days=150), d - timedelta(days=730)]:
            if shifted > date.today() - timedelta(days=10):
                continue
            frame, idx = fetch_frame(lat, lon, shifted.isoformat())
            if frame is None:
                continue
            feats = feature_fn(frame, idx)
            if feats:
                samples.append((feats, 0))
    print(f"  paired negatives done ({len(samples)} total rows)")


def random_negatives(feature_fn, samples, n):
    """random CONUS land points on random dates 2010-2024"""
    added = 0
    while added < n:
        lat = rng.uniform(26.0, 48.5)
        lon = rng.uniform(-123.0, -75.0)
        day = date(rng.randint(2010, 2024), rng.randint(1, 12), rng.randint(1, 28))
        frame, idx = fetch_frame(lat, lon, day.isoformat())
        if frame is None:
            continue
        feats = feature_fn(frame, idx)
        if feats:
            samples.append((feats, 0))
            added += 1
    print(f"  {n} random negatives done")


def train_one(name, samples):
    """fit, cross-validate, and save one hazard model plus its model card"""
    feat_names = sorted(samples[0][0].keys())
    X = np.array([[row[k] for k in feat_names] for row, _ in samples])
    y = np.array([lbl for _, lbl in samples])
    print(f"\n{name}: {len(y)} rows, {int(y.sum())} positives, features: {feat_names}")

    cst = [MONOTONE.get(name, {}).get(k, 0) for k in feat_names]
    model = HistGradientBoostingClassifier(
        max_iter=300, max_depth=4, learning_rate=0.06,
        l2_regularization=1.0, class_weight="balanced", random_state=42,
        monotonic_cst=cst)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    auc = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
    acc = cross_val_score(model, X, y, cv=cv, scoring="balanced_accuracy")
    model.fit(X, y)

    # per-feature medians power the occlusion explainer at inference time
    medians = {k: float(np.median(X[:, i])) for i, k in enumerate(feat_names)}

    # permutation importance tells the model card which inputs matter most
    from sklearn.inspection import permutation_importance
    imp = permutation_importance(model, X, y, n_repeats=8, random_state=42, scoring="roc_auc")
    importances = {k: round(float(imp.importances_mean[i]), 4) for i, k in enumerate(feat_names)}

    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump({"model": model, "features": feat_names, "medians": medians},
                os.path.join(MODEL_DIR, f"{name}.pkl"))
    card = {
        "name": name,
        "algorithm": "HistGradientBoostingClassifier",
        "trained_on": "real historical events + ERA5 reanalysis (Open-Meteo archive)",
        "n_samples": int(len(y)), "n_positives": int(y.sum()),
        "cv_roc_auc_mean": round(float(auc.mean()), 3),
        "cv_roc_auc_std": round(float(auc.std()), 3),
        "cv_balanced_accuracy": round(float(acc.mean()), 3),
        "feature_importances": dict(sorted(importances.items(), key=lambda kv: -kv[1])),
        "trained_at": date.today().isoformat(),
    }
    with open(os.path.join(MODEL_DIR, f"{name}_card.json"), "w") as f:
        json.dump(card, f, indent=2)
    print(f"  AUC {auc.mean():.3f} +/- {auc.std():.3f} | balanced acc {acc.mean():.3f}")
    print(f"  saved {name}.pkl + card")


def samples_from_dataset(name):
    """(rows, status) for the persisted training set.

    the live collection below spends several thousand archive API units and its
    rows used to be thrown away the moment the fit finished, which made a retrain
    a several-hour errand and made honest validation impossible after the fact.
    src.ml.dataset saves them, so prefer that whenever it is finished.

    a half-collected dataset is deliberately refused: it is missing whole
    categories of row rather than a random sample of them, and quietly fitting on
    it would replace a good deployed model with a worse one."""
    from src.ml import dataset
    data = dataset.load(name)
    rows = list((data.get("rows") or {}).values())
    if not rows:
        return None, "empty"
    if not data.get("complete"):
        return None, f"partial ({len(rows)} rows so far)"
    return [(r["x"], r["y"]) for r in rows], "complete"


def main(use_dataset=True):
    for name, events, feature_fn, n_random in (
            ("wildfire", FIRE_EVENTS, F.wildfire_features, 90),
            ("flood", FLOOD_EVENTS, F.flood_features, 60)):
        print(f"\n=== {name} model ===")
        samples, status = samples_from_dataset(name) if use_dataset else (None, "skipped")
        if samples:
            print(f"  using {len(samples)} saved rows from src/ml/datasets/{name}.json")
        elif status.startswith("partial"):
            print(f"  dataset is {status}; keeping the existing {name} model rather than "
                  f"retraining on part of one. finish it with: python -m src.ml.dataset {name}")
            continue
        else:
            samples = []
            collect(events, feature_fn, 1, samples)
            negatives_for(events, feature_fn, samples)
            random_negatives(feature_fn, samples, n_random)
        train_one(name, samples)

    # the validation page is only honest if it describes the models that are
    # actually deployed, so regenerate it from the same rows in the same run
    try:
        from src.ml import validate
        rep = validate.build_and_save()
        for m in rep["models"]:
            if m.get("available"):
                print(f"\nvalidation {m['name']}: AUC {m['roc_auc']}, ECE {m['ece']}, "
                      f"recall {m['confusion']['recall']} at the deployed cutoff")
    except Exception as e:  # a failed report must never lose a good model
        print(f"\ncould not regenerate the validation report: {e}")


if __name__ == "__main__":
    # --live forces the old behaviour: refetch every row from the archive API
    main(use_dataset="--live" not in sys.argv)
