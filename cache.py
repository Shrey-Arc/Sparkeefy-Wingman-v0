"""
cache.py
--------
A dumb, honest cache: same exact messages array (persona + memory context +
history + new message) → same cache key → skip the paid API call and reuse
the last real response for TTL seconds. This is NOT semantic caching (no
embeddings, no fuzzy matching) — it only catches genuinely identical calls,
which is common in practice from things like a Streamlit rerun re-firing the
same request, a user resending the same message, or re-running batch_eval on
an unchanged prompt set. Deliberately simple for v0 — a smarter cache is
future work, not required to prove the cost-saving idea works.
"""

import hashlib
import json
import os
import time
from typing import Optional

import storage_utils as storage

CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache.json")
DEFAULT_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", 3600))


def _cache_key(messages: list) -> str:
    raw = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load() -> dict:
    return storage.safe_read_json(CACHE_PATH, {})


def _save(data: dict):
    storage.atomic_write_json(CACHE_PATH, data, ensure_ascii=False)


def get(messages: list, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Optional[str]:
    """Returns the cached raw model output string, or None on miss/expiry."""
    key = _cache_key(messages)
    data = _load()
    entry = data.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] > ttl_seconds:
        return None
    return entry["raw"]


def set(messages: list, raw_response: str):
    key = _cache_key(messages)
    def mutate(data):
        data[key] = {"raw": raw_response, "ts": time.time()}
        return data
    storage.read_modify_write_json(CACHE_PATH, {}, mutate, ensure_ascii=False)


def clear():
    _save({})


def stats() -> dict:
    data = _load()
    now = time.time()
    live = sum(1 for e in data.values() if now - e["ts"] <= DEFAULT_TTL_SECONDS)
    return {"total_entries": len(data), "live_entries": live, "ttl_seconds": DEFAULT_TTL_SECONDS}
