# tiny cached HTTP layer so we never hammer the free APIs
# every service in src/services goes through fetch_json / fetch_text
import time
import threading
import requests

from src.config import USER_AGENT

_cache = {}
_lock = threading.Lock()

# how long responses stay fresh, in seconds, keyed by rough data type
TTL_LIVE = 120        # alerts, active storms, things that change by the minute
TTL_FORECAST = 900    # weather forecasts refresh hourly upstream anyway
TTL_ARCHIVE = 86400   # historical reanalysis never changes


def _get(url, params, timeout):
    # requests encodes params, one session would be nicer but this is simple and safe
    return requests.get(url, params=params, timeout=timeout,
                        headers={"User-Agent": USER_AGENT, "Accept": "*/*"})


def _get_retry(url, params, timeout):
    """one quick retry on a timeout or 5xx, which are usually momentary. we do
    NOT retry a 429 (rate limit) since that only resets on the hour, and a retry
    would just waste another request against the limit."""
    try:
        resp = _get(url, params, timeout)
        if resp.status_code < 500:
            return resp
    except requests.RequestException:
        resp = None
    time.sleep(0.6)
    try:
        return _get(url, params, timeout)
    except requests.RequestException:
        return resp


def fetch_json(url, params=None, ttl=TTL_FORECAST, timeout=20):
    """GET a JSON endpoint with an in-memory TTL cache, returns None on failure"""
    key = (url, tuple(sorted((params or {}).items())))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        resp = _get_retry(url, params, timeout)
        if resp is None or resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None
    with _lock:
        _cache[key] = (now, data)
        # crude eviction so a long-running server doesn't grow forever
        if len(_cache) > 2000:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:500]
            for k, _ in oldest:
                _cache.pop(k, None)
    return data


def fetch_text(url, params=None, ttl=TTL_FORECAST, timeout=20):
    """same idea but for CSV endpoints like NASA FIRMS"""
    key = ("text", url, tuple(sorted((params or {}).items())))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        resp = _get(url, params, timeout)
        if resp.status_code != 200:
            return None
        data = resp.text
    except Exception:
        return None
    with _lock:
        _cache[key] = (now, data)
    return data
