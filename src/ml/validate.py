# honest performance numbers for the trained hazard models.
#
# the model cards already carried a cross-validated AUC, which answers "can it
# rank a fire day above a calm day". that is the easy question. the ones that
# decide whether a score is safe to act on are harder:
#
#   calibration  when it says 70, does it happen about 70% of the time? a model
#                can rank perfectly and still be wildly overconfident, and a
#                risk number that nobody can read as a probability is a number
#                that will be misread.
#   threshold    at the cutoff the product actually uses, how many real events
#                are missed and how many false alarms are raised? one number
#                cannot be improved without hurting the other, so both are shown.
#   failures     which specific events does it get wrong, and do they have
#                something in common?
#
# everything here comes from out-of-fold predictions: each row is scored by a
# model that never saw it during training. in-sample numbers on 330 rows would
# look wonderful and mean nothing.
import json
import os
import time

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold

from src.ml import dataset
from src.ml.train import MONOTONE

OUT_DIR = os.path.join(os.path.dirname(__file__), "models")

# the score above which the product calls something elevated. the module pages
# and the national grid both speak in bands starting at 50, so that is the
# threshold the confusion matrix has to answer for.
DECISION_THRESHOLD = 0.50

# reliability-diagram bins. ten is the convention and it keeps 20-40 rows per
# bin at this dataset size, which is few enough to be noisy and enough to read.
N_BINS = 10


def _model(name, n_features_order):
    cst = [MONOTONE.get(name, {}).get(k, 0) for k in n_features_order]
    return HistGradientBoostingClassifier(
        max_iter=300, max_depth=4, learning_rate=0.06,
        l2_regularization=1.0, class_weight="balanced", random_state=42,
        monotonic_cst=cst)


def _roc(y, p):
    """(fpr, tpr) at every distinct threshold, plus the area under it"""
    order = np.argsort(-p)
    y = np.asarray(y)[order]
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if pos == 0 or neg == 0:
        return [], 0.5
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    tpr = np.concatenate([[0.0], tp / pos])
    fpr = np.concatenate([[0.0], fp / neg])
    auc = float(np.trapezoid(tpr, fpr)) if hasattr(np, "trapezoid") else float(np.trapz(tpr, fpr))
    pts = [[round(float(a), 4), round(float(b), 4)] for a, b in zip(fpr, tpr)]
    return _thin(pts), auc


def _pr(y, p):
    """precision-recall, which is the honest curve when positives are the
    minority: a 76% negative dataset makes ROC look better than life"""
    order = np.argsort(-p)
    y = np.asarray(y)[order]
    pos = int(y.sum())
    if pos == 0:
        return [], 0.0
    tp = np.cumsum(y)
    k = np.arange(1, len(y) + 1)
    precision = tp / k
    recall = tp / pos
    ap = float(np.sum((recall - np.concatenate([[0.0], recall[:-1]])) * precision))
    pts = [[round(float(r), 4), round(float(pr), 4)] for r, pr in zip(recall, precision)]
    return _thin(pts), ap


def _thin(points, keep=120):
    """curves with 330 vertices draw the same as curves with 120 and weigh three
    times as much over the wire"""
    if len(points) <= keep:
        return points
    step = len(points) / keep
    out = [points[int(i * step)] for i in range(keep)]
    out.append(points[-1])
    return out


def _calibration(y, p, n_bins=N_BINS):
    """reliability diagram plus the expected calibration error"""
    y = np.asarray(y, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins, ece = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (p >= lo) & (p < hi if hi < 1.0 else p <= hi)
        n = int(sel.sum())
        if n == 0:
            bins.append({"lo": round(float(lo), 2), "hi": round(float(hi), 2),
                         "n": 0, "predicted": None, "observed": None})
            continue
        pred = float(p[sel].mean())
        obs = float(y[sel].mean())
        ece += (n / len(y)) * abs(pred - obs)
        bins.append({"lo": round(float(lo), 2), "hi": round(float(hi), 2), "n": n,
                     "predicted": round(pred, 3), "observed": round(obs, 3)})
    return bins, round(ece, 4)


def _confusion(y, p, threshold=DECISION_THRESHOLD):
    y = np.asarray(y)
    pred = (p >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    recall = tp / (tp + fn) if tp + fn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": threshold,
        "true_positive": tp, "false_positive": fp,
        "false_negative": fn, "true_negative": tn,
        "recall": round(recall, 3), "precision": round(precision, 3),
        "specificity": round(specificity, 3), "f1": round(f1, 3),
        "balanced_accuracy": round((recall + specificity) / 2, 3),
    }


def _threshold_table(y, p):
    """what you buy and what you pay at each possible cutoff. this is the table
    that makes the tradeoff concrete instead of rhetorical."""
    rows = []
    for t in [round(x * 0.05, 2) for x in range(2, 19)]:
        c = _confusion(y, p, t)
        rows.append({"threshold": t, "recall": c["recall"], "precision": c["precision"],
                     "false_positive": c["false_positive"],
                     "false_negative": c["false_negative"],
                     "f1": c["f1"], "balanced_accuracy": c["balanced_accuracy"]})
    return rows


# below this, cross-validation is measuring the split more than the model
MIN_ROWS = 60


def out_of_fold(name, n_splits=5):
    """predict every row with a model that never trained on it"""
    feat_names, X, y, meta = dataset.matrix(name)
    if not X or len(X) < MIN_ROWS:
        return None
    X = np.array(X, dtype=float)
    y = np.array(y, dtype=int)
    if y.sum() < n_splits or (len(y) - y.sum()) < n_splits:
        return None

    oof = np.zeros(len(y), dtype=float)
    folds = np.zeros(len(y), dtype=int)
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    for k, (train_idx, test_idx) in enumerate(cv.split(X, y)):
        m = _model(name, feat_names)
        m.fit(X[train_idx], y[train_idx])
        oof[test_idx] = m.predict_proba(X[test_idx])[:, 1]
        folds[test_idx] = k
    return {"features": feat_names, "X": X, "y": y, "p": oof,
            "folds": folds, "meta": meta}


def evaluate(name):
    """the whole report for one model"""
    fit = out_of_fold(name)
    if fit is None:
        have = len(dataset.load(name).get("rows") or {})
        return {"name": name, "available": False,
                "reason": f"Only {have} training rows have been collected so far, and "
                          f"cross-validation below {MIN_ROWS} measures the split more than "
                          f"the model. This fills in as collection finishes."}

    # a dataset still being collected is missing whole categories of row rather
    # than a random subset, so the numbers below are provisional and say so
    complete = bool(dataset.load(name).get("complete"))

    y, p, meta = fit["y"], fit["p"], fit["meta"]
    roc_pts, auc = _roc(y, p)
    pr_pts, ap = _pr(y, p)
    bins, ece = _calibration(y, p)
    brier = float(np.mean((p - y) ** 2))
    # the score a coin-flip-rate constant predictor would get. a Brier of 0.12
    # means nothing until you know the base rate it has to beat.
    base_rate = float(y.mean())
    brier_ref = base_rate * (1 - base_rate)

    # per-fold AUC gives the spread, which is the honest error bar on a dataset
    # this small. a single number hides that fold 3 might be much worse.
    fold_aucs = []
    for k in sorted(set(fit["folds"].tolist())):
        sel = fit["folds"] == k
        if len(set(y[sel].tolist())) < 2:
            continue
        fold_aucs.append(round(_roc(y[sel], p[sel])[1], 3))

    # the rows it gets most wrong, both ways round
    errors = sorted(({"date": meta[i]["date"], "lat": meta[i]["lat"],
                      "lon": meta[i]["lon"], "label": int(y[i]),
                      "predicted": round(float(p[i]), 3),
                      "kind": "missed event" if y[i] == 1 else "false alarm"}
                     for i in range(len(y))
                     if (y[i] == 1 and p[i] < DECISION_THRESHOLD)
                     or (y[i] == 0 and p[i] >= DECISION_THRESHOLD)),
                    key=lambda r: abs(r["predicted"] - r["label"]), reverse=True)

    return {
        "name": name, "available": True, "dataset_complete": complete,
        "caveat": None if complete else
                  ("This dataset is still being collected, so these numbers are provisional "
                   "and will move as the remaining rows arrive."),
        "n_samples": int(len(y)), "n_positives": int(y.sum()),
        "base_rate": round(base_rate, 3),
        "roc_auc": round(auc, 3), "fold_auc": fold_aucs,
        "fold_auc_std": round(float(np.std(fold_aucs)), 3) if fold_aucs else None,
        "average_precision": round(ap, 3),
        "brier": round(brier, 4),
        "brier_reference": round(brier_ref, 4),
        "brier_skill": round(1 - brier / brier_ref, 3) if brier_ref else None,
        "ece": ece,
        "roc_curve": roc_pts, "pr_curve": pr_pts,
        "calibration": bins,
        "confusion": _confusion(y, p),
        "thresholds": _threshold_table(y, p),
        "errors": errors[:12],
        "features": fit["features"],
        "evaluated_at": time.time(),
        "method": ("stratified 5-fold cross-validation; every row scored by a "
                   "model that never saw it in training"),
    }


def report(names=("wildfire", "flood")):
    return {"models": [evaluate(n) for n in names], "generated_at": time.time(),
            "decision_threshold": DECISION_THRESHOLD}


REPORT_FILE = os.path.join(OUT_DIR, "validation.json")


def load():
    """the last computed report, or None. the page reads this: cross-validating
    two models takes about ten seconds, which is too slow for a page load and
    far too slow to repeat for every visitor."""
    try:
        with open(REPORT_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def build_and_save(names=("wildfire", "flood")):
    rep = report(names)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, separators=(",", ":"))
    return rep


if __name__ == "__main__":  # python -m src.ml.validate
    rep = build_and_save()
    for m in rep["models"]:
        if not m.get("available"):
            print(f"{m['name']}: {m['reason']}")
            continue
        print(f"\n{m['name']}: {m['n_samples']} rows, {m['n_positives']} positives")
        print(f"  ROC AUC        {m['roc_auc']}  (folds {m['fold_auc']})")
        print(f"  avg precision  {m['average_precision']}")
        print(f"  Brier          {m['brier']}  vs {m['brier_reference']} baseline "
              f"(skill {m['brier_skill']})")
        print(f"  calibration    ECE {m['ece']}")
        c = m["confusion"]
        print(f"  at {c['threshold']:.2f}: recall {c['recall']}  precision {c['precision']}  "
              f"({c['false_negative']} missed, {c['false_positive']} false alarms)")
