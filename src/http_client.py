# tiny cached HTTP layer so we never hammer the free APIs
# every service in src/services goes through fetch_json / fetch_text
#
# two properties matter more than speed here:
#   1. stale-while-error. a free API that rate-limits us must never turn into
#      "no data available" on the page. once a response has been seen, it stays
#      servable forever and only gets *refreshed* when the upstream cooperates.
#   2. the cache survives a restart. it is written to disk so a dev restart or a
#      Render redeploy does not start from a cold, rate-limited state.
import atexit
import os
import pickle
import tempfile
import threading
import time

import requests

from src.config import USER_AGENT

_cache = {}
_lock = threading.Lock()

# how long responses stay fresh, in seconds, keyed by rough data type
TTL_LIVE = 120        # alerts, active storms, things that change by the minute
TTL_FORECAST = 900    # weather forecasts refresh hourly upstream anyway
TTL_ARCHIVE = 86400   # historical reanalysis never changes

# an entry older than this is dropped rather than served stale: past a week even
# a forecast is worthless, and we would rather say so than lie
MAX_STALE = 7 * 86400

CACHE_DIR = os.environ.get(
    "TERRASIGHT_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache"))
CACHE_FILE = os.path.join(CACHE_DIR, "http_cache.pkl")

# entries bigger than this are kept in memory but never written to disk, so one
# national grid pull cannot balloon the cache file
MAX_DISK_ENTRY_BYTES = 4_000_000

# counters the /api/health endpoint reports, so data problems are visible
stats = {"hits": 0, "misses": 0, "stale_served": 0, "failures": 0, "rate_limited": 0}

# when a host tells us we are over an hourly quota, record when that quota
# actually resets. bulk jobs read this and stop early instead of spending
# hundreds of requests learning the same thing one 429 at a time.
_rate_limits = {}


def _host(url):
    return url.split("//", 1)[-1].split("/", 1)[0]


def _note_rate_limit(url, resp):
    stats["rate_limited"] += 1
    reason = ""
    try:
        reason = str((resp.json() or {}).get("reason") or "")
    except Exception:
        reason = (resp.text or "")[:200]
    # an hourly cap clears at the top of the hour; a minute cap clears far
    # sooner, and treating one like the other wastes either quota or time
    now = time.time()
    if "hour" in reason.lower():
        until = now + (3600 - (now % 3600)) + 5
    elif "daily" in reason.lower():
        until = now + 1800     # re-probe periodically rather than idling all day
    else:
        until = now + 65
    with _lock:
        prev = _rate_limits.get(_host(url), (0.0, ""))
        if until > prev[0]:
            _rate_limits[_host(url)] = (until, reason)


def rate_limited_for(url):
    """seconds until this host's quota resets, or 0 if it is not limited"""
    with _lock:
        until, _ = _rate_limits.get(_host(url), (0.0, ""))
    return max(0.0, until - time.time())


# ---------------------------------------------------------------- disk cache

def _load_disk():
    try:
        with open(CACHE_FILE, "rb") as fh:
            data = pickle.load(fh)
    except Exception:
        return
    now = time.time()
    with _lock:
        for k, v in data.items():
            if now - v[0] < MAX_STALE:
                _cache[k] = v


_dirty = False
_last_save = 0.0


def _save_disk(force=False):
    """write the cache out, debounced to at most once a minute"""
    global _dirty, _last_save
    now = time.time()
    with _lock:
        if not force and (not _dirty or now - _last_save < 60):
            return
        snapshot = {}
        for k, v in _cache.items():
            if now - v[0] >= MAX_STALE:
                continue
            snapshot[k] = v
        _dirty = False
        _last_save = now
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # write to a temp file and replace, so a crash mid-write cannot corrupt
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            trimmed = {}
            for k, v in snapshot.items():
                try:
                    blob = pickle.dumps(v, protocol=pickle.HIGHEST_PROTOCOL)
                except Exception:
                    continue
                if len(blob) <= MAX_DISK_ENTRY_BYTES:
                    trimmed[k] = v
            pickle.dump(trimmed, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass  # a cache that cannot be persisted is not a reason to fail a request


_load_disk()
atexit.register(lambda: _save_disk(force=True))


# ---------------------------------------------------------------- transport

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})


def _get(url, params, timeout):
    return _session.get(url, params=params, timeout=timeout)


def _get_retry(url, params, timeout, attempts=3):
    """retry timeouts and 5xx with a short backoff. we do NOT retry a 429 (rate
    limit) since that only resets on the hour and a retry just burns quota."""
    resp = None
    for attempt in range(attempts):
        try:
            resp = _get(url, params, timeout)
            if resp.status_code < 500 or resp.status_code == 429:
                return resp
        except requests.RequestException:
            resp = None
        if attempt < attempts - 1:
            time.sleep(0.5 * (2 ** attempt))
    return resp


def _remember(key, data):
    global _dirty
    now = time.time()
    with _lock:
        _cache[key] = (now, data)
        _dirty = True
        # crude eviction so a long-running server does not grow forever
        if len(_cache) > 4000:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:1000]
            for k, _ in oldest:
                _cache.pop(k, None)
    _save_disk()


def _lookup(key, ttl):
    """returns (value, is_fresh). a hit past its ttl still comes back so the
    caller can serve it when the network refresh fails."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if not hit:
        return None, False
    age = now - hit[0]
    if age >= MAX_STALE:
        return None, False
    return hit[1], age < ttl


def fetch_json(url, params=None, ttl=TTL_FORECAST, timeout=20):
    """GET a JSON endpoint with a TTL cache that falls back to stale data.

    returns None only when the endpoint has never answered successfully."""
    key = (url, tuple(sorted((params or {}).items())))
    cached, fresh = _lookup(key, ttl)
    if fresh:
        stats["hits"] += 1
        return cached

    stats["misses"] += 1
    try:
        resp = _get_retry(url, params, timeout)
        if resp is not None and resp.status_code == 429:
            _note_rate_limit(url, resp)
        if resp is None or resp.status_code != 200:
            raise ValueError(f"status {getattr(resp, 'status_code', 'none')}")
        data = resp.json()
    except Exception:
        stats["failures"] += 1
        if cached is not None:
            # the whole point: a busy upstream degrades to slightly old data,
            # never to an empty panel
            stats["stale_served"] += 1
            return cached
        return None
    _remember(key, data)
    return data


def fetch_text(url, params=None, ttl=TTL_FORECAST, timeout=20):
    """same idea but for CSV endpoints like NASA FIRMS"""
    key = ("text", url, tuple(sorted((params or {}).items())))
    cached, fresh = _lookup(key, ttl)
    if fresh:
        return cached
    try:
        resp = _get_retry(url, params, timeout, attempts=2)
        if resp is None or resp.status_code != 200:
            raise ValueError("bad status")
        data = resp.text
    except Exception:
        return cached
    _remember(key, data)
    return data


def cache_age(url, params=None):
    """seconds since this exact request was last refreshed, or None"""
    key = (url, tuple(sorted((params or {}).items())))
    with _lock:
        hit = _cache.get(key)
    return None if not hit else time.time() - hit[0]
