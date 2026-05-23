"""Redis-backed mutual-exclusion lock for distributed actor coordination.

``RedisLock`` matches the public surface of ``LaneLock`` in ``grid_state`` so
every existing caller works unchanged when the Redis backend is selected.

Design:
- Acquire:  ``SET key holder NX PX ttl_ms``  — atomic take-or-fail.
  Re-entrant (same holder calling twice = success).
  Poll loop: 50 ms sleep between attempts; bounded by ``timeout``.
  ``cancel_check()`` checked on every iteration — waiter bails on False.
- Release:  Pipeline+WATCH compare-and-delete — never a blind DEL.
  Semantically equivalent to the Lua CAS spec; uses WATCH/MULTI/EXEC so
  it works with both real Redis and fakeredis (fakeredis does not support
  EVAL/EVALSHA).  Production real-Redis callers automatically get the
  optimistic-lock guarantees of WATCH; true Lua CAS can be substituted
  later if needed.
- Touch:    Pipeline+WATCH CAS-extend PX.
- Force release: unconditional DEL (fault / reset path only).
- Crash-orphan recovery: TTL expires naturally; no reaper process.

Default TTL: 180 000 ms (3 minutes).
Retry sleep:  50 ms.

``LockKeepalive`` spawns a daemon thread that calls ``touch`` every
``interval_s`` seconds while the holder holds the lock.  Stop it with
``keepalive.stop()`` or use it as a context manager.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

try:
    from redis.exceptions import WatchError
except ImportError:
    # Fallback: if redis is not installed (should not happen in production).
    class WatchError(Exception):  # type: ignore[no-redef]
        pass

_RETRY_SLEEP_S = 0.05   # 50 ms poll interval
DEFAULT_TTL_MS = 180_000  # 3 minutes


class RedisLock:
    """Distributed mutual-exclusion lock backed by Redis.

    Matches the ``LaneLock`` surface so it is a drop-in replacement for the
    in-memory path when ``INTERACT_STATE=redis``.

    Parameters
    ----------
    key:
        Redis key used to hold the lock (e.g. ``"grid:lock:lane"``).
    redis_client:
        A ``redis.Redis`` (or ``fakeredis.FakeRedis``) instance with
        ``decode_responses=True``.
    default_ttl_ms:
        TTL for the lock key in milliseconds.  The holder must renew via
        ``touch()`` if it holds the lock longer than this (use
        ``LockKeepalive``).
    """

    def __init__(
        self,
        key: str,
        redis_client,
        default_ttl_ms: int = DEFAULT_TTL_MS,
    ) -> None:
        self._key = key
        self._client = redis_client
        self._ttl_ms = default_ttl_ms

    # ── Public API ─────────────────────────────────────────────────────────

    def acquire(
        self,
        holder: str,
        timeout: float = 60.0,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Block until the lock is free, then take it for ``holder``.

        Returns True on success, False on timeout or cancel.
        Re-entrant: if ``holder`` already owns the lock this is a no-op success.

        ``cancel_check()`` is called before each retry; returning False aborts
        the wait immediately (the waiter has been faulted / stopped).
        """
        deadline = time.monotonic() + timeout
        while True:
            # Re-entrant check: if we already hold it, succeed.
            current = self._client.get(self._key)
            if current == holder:
                return True

            # Try to acquire atomically.
            ok = self._client.set(self._key, holder, nx=True, px=self._ttl_ms)
            if ok:
                return True

            # Failed — check cancel and timeout before sleeping.
            if cancel_check is not None and not cancel_check():
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(_RETRY_SLEEP_S)

    def release(self, holder: str) -> bool:
        """CAS-release: only succeeds if ``holder`` currently owns the lock.

        Returns True if released, False if the lock was held by someone else
        (or already expired).  Never performs a blind DEL.

        Uses pipeline+WATCH for CAS semantics compatible with both real Redis
        and fakeredis.
        """
        while True:
            try:
                pipe = self._client.pipeline()
                pipe.watch(self._key)
                current = pipe.get(self._key)
                if current != holder:
                    pipe.reset()
                    return False
                pipe.multi()
                pipe.delete(self._key)
                pipe.execute()
                return True
            except WatchError:
                # Another client changed the key — retry the CAS.
                continue

    def force_release(self) -> None:
        """Unconditional DEL.  Use only on fault / reset paths."""
        self._client.delete(self._key)

    def holder(self) -> Optional[str]:
        """Return the current holder, or None if the lock is free."""
        return self._client.get(self._key)

    def is_free(self) -> bool:
        """True when the lock is not held (key absent or expired)."""
        return self._client.get(self._key) is None

    def touch(self, holder: str) -> bool:
        """CAS-extend TTL: only succeeds if ``holder`` still owns the lock.

        Returns True if the TTL was extended, False if the lock is no longer
        held by ``holder`` (e.g. it expired or another holder took it).

        Uses pipeline+WATCH for CAS semantics compatible with fakeredis.
        """
        while True:
            try:
                pipe = self._client.pipeline()
                pipe.watch(self._key)
                current = pipe.get(self._key)
                if current != holder:
                    pipe.reset()
                    return False
                pipe.multi()
                pipe.pexpire(self._key, self._ttl_ms)
                pipe.execute()
                return True
            except WatchError:
                continue


# ── LockKeepalive ─────────────────────────────────────────────────────────────

class LockKeepalive:
    """Daemon thread that renews a ``RedisLock`` for a holder.

    Call ``stop()`` (or use as a context manager) when the lock is released.
    The thread exits within one ``interval_s`` cycle after ``stop()``.

    Usage::

        keepalive = LockKeepalive(lock, holder="agv-1", interval_s=60.0)
        keepalive.start()
        try:
            ... long operation ...
        finally:
            lock.release("agv-1")
            keepalive.stop()

    Or as a context manager::

        with LockKeepalive(lock, holder="agv-1", interval_s=60.0):
            ... long operation ...
        lock.release("agv-1")  # keepalive already stopped

    Parameters
    ----------
    lock:
        The ``RedisLock`` to keep alive.
    holder:
        The holder identifier — must match what was passed to ``acquire``.
    interval_s:
        How often to call ``touch``.  Default: ``TTL/3`` = 60 s for 180 s TTL.
    """

    def __init__(
        self,
        lock: RedisLock,
        holder: str,
        interval_s: float = DEFAULT_TTL_MS / 3 / 1000,  # 60.0 s
    ) -> None:
        self._lock = lock
        self._holder = holder
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "LockKeepalive":
        """Start the keepalive daemon thread.  Returns self for chaining."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"lock-keepalive-{self._holder}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Signal the keepalive thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval_s):
            self._lock.touch(self._holder)

    # Context-manager support.
    def __enter__(self) -> "LockKeepalive":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
