# the labeled feature rows the hazard models are trained and validated on.
#
# train.py used to collect these and throw them away, which meant every honest
# question about the models ("how well calibrated is it? where does it fail?")
# required another hour of archive fetches. the sampling design is unchanged,
# it is just written down now: same events, same paired negatives, same seed,
# so a cached dataset is byte-identical to what a retrain would collect.
#
# COST. each row is one 131-day ERA5 archive pull, about 10 Open-Meteo units.
# the full pair of datasets is roughly 5,000 units, which is the entire free
# hourly allowance, so collection is paced and resumable: rows land in the file
# as they are fetched and a second run picks up exactly where the first stopped.
import json
import os
import random
import time
from datetime import date, timedelta

from src import http_client, config
from src.data.fire_history import FIRE_EVENTS
from src.data.flood_history import FLOOD_EVENTS
from src.ml import features as F
from src.services import open_meteo

DATA_DIR = os.path.join(os.path.dirname(__file__), "datasets")

# the same seed train.py used, so the random negatives are the same points
SEED = 42

# one archive pull covers 131 days -> ceil(131/14) = 10 units
UNITS_PER_ROW = 10
UNITS_PER_HOUR = 4200

SPECS = {
    "wildfire": {"events": FIRE_EVENTS, "features": F.wildfire_features, "n_random": 90},
    "flood": {"events": FLOOD_EVENTS, "features": F.flood_features, "n_random": 60},
}


def _path(name):
    return os.path.join(DATA_DIR, f"{name}.json")


def _fetch_frame(lat, lon, target_date):
    """ERA5 daily history for the 130 days ending 3 days after the event"""
    d = date.fromisoformat(target_date)
    start = (d - timedelta(days=127)).isoformat()
    end = min(d + timedelta(days=3), date.today() - timedelta(days=6)).isoformat()
    resp = open_meteo.archive(lat, lon, start, end, daily=F.DAILY_VARS, hourly=F.HOURLY_VARS)
    frame = F.daily_frame(resp)
    if not frame:
        return None, None
    try:
        return frame, frame["time"].index(target_date)
    except ValueError:
        return None, None


def plan(name):
    """every row the dataset should contain, as (row_id, lat, lon, date, label).

    deterministic: the same call always produces the same list in the same
    order, which is what lets a paced collection resume without duplicating or
    skipping anything."""
    spec = SPECS[name]
    rows = []
    for ev_name, day, lat, lon in spec["events"]:
        rows.append((f"pos:{ev_name}:{day}", lat, lon, day, 1))
    # counter-examples: the same spot 5 months earlier, and 2 years earlier
    for ev_name, day, lat, lon in spec["events"]:
        d = date.fromisoformat(day)
        for shifted in (d - timedelta(days=150), d - timedelta(days=730)):
            if shifted > date.today() - timedelta(days=10):
                continue
            rows.append((f"neg:{ev_name}:{shifted.isoformat()}", lat, lon,
                         shifted.isoformat(), 0))
    # random CONUS land points on random dates. train.py drew these until it had
    # n_random *successful* rows; drawing a fixed larger pool and taking the
    # first n that resolve is the same distribution and is resumable.
    rng = random.Random(SEED)
    for i in range(spec["n_random"] * 3):
        lat = rng.uniform(26.0, 48.5)
        lon = rng.uniform(-123.0, -75.0)
        day = date(rng.randint(2010, 2024), rng.randint(1, 12), rng.randint(1, 28))
        rows.append((f"rand:{i}", round(lat, 4), round(lon, 4), day.isoformat(), 0))
    return rows


def load(name):
    try:
        with open(_path(name), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"name": name, "rows": {}, "dead": [], "started_at": time.time()}


def save(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, _path(name))


def target_rows(name):
    """how many rows a complete dataset has: every event and paired negative
    that resolves, plus exactly n_random randoms"""
    spec = SPECS[name]
    fixed = sum(1 for r in plan(name) if not r[0].startswith("rand:"))
    return fixed, spec["n_random"]


def collect(name, progress=None, budget=None):
    """fetch whatever rows are still missing, pacing under the hourly quota.

    returns (data, complete). safe to call repeatedly: finished rows are read
    straight from the file and cost nothing."""
    data = load(name)
    rows, dead = data["rows"], set(data.get("dead") or [])
    _, want_random = target_rows(name)
    spent = 0
    limit = budget if budget is not None else UNITS_PER_HOUR

    have_random = sum(1 for k in rows if k.startswith("rand:"))
    for row_id, lat, lon, day, label in plan(name):
        if row_id in rows or row_id in dead:
            continue
        if row_id.startswith("rand:") and have_random >= want_random:
            continue
        if spent + UNITS_PER_ROW > limit:
            return data, False
        if http_client.rate_limited_for(config.OPEN_METEO_ARCHIVE) > 0:
            return data, False

        frame, idx = _fetch_frame(lat, lon, day)
        spent += UNITS_PER_ROW
        feats = SPECS[name]["features"](frame, idx) if frame is not None else None
        if not feats:
            # the archive has no usable window here. record it so a resume does
            # not spend another ten units rediscovering that.
            dead.add(row_id)
        else:
            rows[row_id] = {"x": feats, "y": label, "lat": lat, "lon": lon, "date": day}
            if row_id.startswith("rand:"):
                have_random += 1
        data["dead"] = sorted(dead)
        save(name, data)
        if progress:
            progress(name, len(rows), spent)
        # the archive endpoint is stricter than the forecast one; a small gap
        # between 10-unit calls keeps us clear of its per-minute ceiling
        time.sleep(1.0)

    data["complete"] = True
    save(name, data)
    return data, True


def matrix(name):
    """(feature_names, X, y, meta) for whatever rows exist right now"""
    data = load(name)
    rows = list(data["rows"].values())
    if not rows:
        return [], [], [], []
    feat_names = sorted(rows[0]["x"].keys())
    X = [[r["x"].get(k) for k in feat_names] for r in rows]
    y = [r["y"] for r in rows]
    meta = [{"lat": r["lat"], "lon": r["lon"], "date": r["date"]} for r in rows]
    return feat_names, X, y, meta


if __name__ == "__main__":  # python -m src.ml.dataset [wildfire|flood]
    import sys
    names = sys.argv[1:] or list(SPECS)
    for n in names:
        fixed, rand = target_rows(n)
        print(f"{n}: target about {fixed + rand} rows")
        d, done = collect(n, progress=lambda nm, have, spent: print(
            f"  {nm}: {have} rows, {spent} units spent", end="\r", flush=True))
        print(f"\n  {len(d['rows'])} rows, {len(d.get('dead') or [])} unusable, "
              f"complete={done}")
