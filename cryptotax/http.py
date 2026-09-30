"""Cached, rate-limited HTTP. Raw responses are stored under data/cache so a
re-run never refetches and every number can be traced to its source."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests

CACHE = Path("data/cache")
_last: dict[str, float] = {}


def _throttle(host: str, per_sec: float) -> None:
    wait = _last.get(host, 0) + 1 / per_sec - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.monotonic()


def fetch(method: str, url: str, *, params=None, body=None, per_sec: float = 4,
          cache: bool = True, redact: tuple[str, ...] = ("apikey",)):
    key_src = json.dumps([method, url, {k: v for k, v in (params or {}).items() if k not in redact}, body],
                         sort_keys=True)
    path = CACHE / hashlib.sha256(key_src.encode()).hexdigest()[:32]
    if cache and path.exists():
        return json.loads(path.read_text())
    host = url.split("/")[2]
    for attempt in range(6):
        _throttle(host, per_sec)
        try:
            r = requests.request(method, url, params=params, json=body, timeout=60)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        data = r.json()
        if cache:
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        return data
    raise RuntimeError(f"giving up on {url}")
