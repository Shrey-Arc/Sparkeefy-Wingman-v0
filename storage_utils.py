"""
storage_utils.py
-----------------
Two real failure modes showed up under actual testing that neither memory.py
nor cache.py fully guarded against:

1. Corrupted reads: a truncated/partial JSON file (from a crash mid-write,
   or an interrupted process) previously crashed the whole app on the next
   read — conversations.json gets written on every single chat message, so
   this was a realistic, high-frequency way to take the app down entirely
   until someone manually deleted the file.
2. Non-atomic writes: `open(path, "w")` followed by `json.dump()` briefly
   leaves the file in a half-written state. If the process is killed or
   crashes in that window (e.g. a `--reload` restart, Ctrl+C, an OOM kill),
   the file is left corrupted — which then triggers failure mode #1 on the
   next read.

Both memory.py and cache.py now go through this module instead of raw
open()/json.load()/json.dump() calls.
"""

import json
import os
import shutil
import sys
import threading
import time

_locks_guard = threading.Lock()
_file_locks: dict = {}


def get_lock(path: str) -> threading.RLock:
    """
    One reentrant lock per absolute file path, created lazily. RLock (not
    Lock) matters here: safe_read_json() and atomic_write_json() each
    acquire this internally, and callers doing a proper read-modify-write
    need to hold the SAME lock across both calls to avoid a lost-update race
    (thread A reads, thread B reads stale data, A writes, B writes and clobbers
    A's update). With a plain Lock, a caller wrapping read+write in the lock
    would deadlock the moment safe_read_json tried to acquire it again from
    the same thread. RLock allows that re-entry safely.

    This only serializes access within one process — it does not protect
    against two separate OS processes writing the same file, which isn't
    this app's deployment shape (a single uvicorn process for the v0 backend).
    """
    abspath = os.path.abspath(path)
    with _locks_guard:
        if abspath not in _file_locks:
            _file_locks[abspath] = threading.RLock()
        return _file_locks[abspath]


def safe_read_json(path: str, default):
    """
    Reads JSON from `path`. On missing file, returns `default` (a fresh
    copy — callers get their own dict/list, not a shared mutable default).
    On corrupted/unparseable content, backs the bad file up alongside itself
    (so it's recoverable for debugging, not silently destroyed), prints a
    clear warning, and returns `default` instead of raising — a bad file
    should degrade the feature it belongs to, not crash the whole app.
    """
    if not os.path.exists(path):
        return _fresh_copy(default)

    with get_lock(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            backup_path = f"{path}.corrupted-{int(time.time())}"
            try:
                shutil.copy2(path, backup_path)
            except OSError:
                backup_path = None
            print(
                f"[storage_utils] WARNING: {path} was corrupted ({e}). "
                f"Resetting to default." + (f" Bad copy saved to {backup_path}." if backup_path else ""),
                file=sys.stderr,
            )
            return _fresh_copy(default)


def atomic_write_json(path: str, data, **dump_kwargs):
    """
    Writes JSON atomically: serialize to a temp file in the same directory,
    then os.replace() it over the target. os.replace() is atomic on both
    POSIX and Windows, so a crash mid-write leaves either the old file or
    the new one intact — never a half-written one.
    """
    with get_lock(path):
        directory = os.path.dirname(os.path.abspath(path))
        tmp_path = os.path.join(directory, f".{os.path.basename(path)}.tmp-{os.getpid()}-{time.time_ns()}")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, **dump_kwargs)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def read_modify_write_json(path: str, default, mutator, **dump_kwargs):
    """
    Atomic read-modify-write in one call: holds the file's lock across the
    read, the mutation, and the write, so concurrent callers can't interleave
    and silently lose an update. `mutator(data) -> data` receives whatever
    safe_read_json would have returned and must return the value to persist.

    This is the pattern memory.py's append_turn/upsert/clear_history/
    approve_candidate should use instead of separate load-then-save calls,
    which had a real lost-update race under concurrent requests (FastAPI
    runs sync route handlers in a thread pool, so two /chat calls for the
    same relationship can genuinely run concurrently).
    """
    with get_lock(path):
        data = safe_read_json(path, default)
        data = mutator(data)
        atomic_write_json(path, data, **dump_kwargs)
        return data


def _fresh_copy(default):
    if isinstance(default, dict):
        return dict(default)
    if isinstance(default, list):
        return list(default)
    return default
