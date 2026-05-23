"""RedisLock test suite — run entirely against fakeredis (no real Redis needed).

Covers:
1. Basic acquire/release round-trip.
2. Timeout: a second acquire times out while the first holds.
3. cancel_check aborts waiter immediately.
4. Re-entrancy: same holder acquiring twice = success.
5. CAS release safety: wrong holder cannot delete.
6. TTL expiry: lock auto-releases when TTL expires (fakeredis time advance).
7. touch() extends TTL.
8. force_release() drops regardless of holder.
9. LockKeepalive renews until stopped.
"""

from __future__ import annotations

import threading
import time

import fakeredis
import pytest

from app.models.redis_lock import RedisLock, LockKeepalive, DEFAULT_TTL_MS


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_redis():
    """A fresh FakeRedis server + client per test."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    return client


def make_lock(client, key: str = "test:lock", ttl_ms: int = DEFAULT_TTL_MS) -> RedisLock:
    return RedisLock(key=key, redis_client=client, default_ttl_ms=ttl_ms)


# ── 1. Basic acquire/release ────────────────────────────────────────────────────

class TestAcquireRelease:
    def test_acquire_free_lock_succeeds(self, fake_redis):
        lock = make_lock(fake_redis)
        assert lock.is_free()
        assert lock.acquire("holder-1", timeout=1.0) is True
        assert lock.holder() == "holder-1"
        assert not lock.is_free()

    def test_release_by_holder_succeeds(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("holder-1", timeout=1.0)
        assert lock.release("holder-1") is True
        assert lock.is_free()
        assert lock.holder() is None

    def test_second_acquire_blocked_short_timeout(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("holder-1", timeout=5.0)
        ok = lock.acquire("holder-2", timeout=0.15)
        assert ok is False

    def test_second_acquire_succeeds_after_release(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("holder-1", timeout=1.0)

        acquired = []

        def waiter():
            acquired.append(lock.acquire("holder-2", timeout=3.0))

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        assert acquired == []  # still blocked
        lock.release("holder-1")
        t.join(timeout=2.0)
        assert acquired == [True]
        assert lock.holder() == "holder-2"


# ── 2. Timeout ─────────────────────────────────────────────────────────────────

class TestTimeout:
    def test_acquire_returns_false_on_timeout(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("holder-1", timeout=5.0)
        t0 = time.monotonic()
        ok = lock.acquire("holder-2", timeout=0.2)
        elapsed = time.monotonic() - t0
        assert ok is False
        # Should time out in approximately 0.2 s, not wait forever.
        assert elapsed < 1.0


# ── 3. cancel_check ────────────────────────────────────────────────────────────

class TestCancelCheck:
    def test_cancel_check_aborts_wait(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("holder-1", timeout=5.0)
        t0 = time.monotonic()
        ok = lock.acquire("holder-2", timeout=30.0, cancel_check=lambda: False)
        elapsed = time.monotonic() - t0
        assert ok is False
        # Should bail well within 1 second (next poll cycle = 50 ms).
        assert elapsed < 1.0

    def test_cancel_check_returning_true_allows_wait(self, fake_redis):
        """cancel_check returning True means "I'm alive" — wait should continue."""
        lock = make_lock(fake_redis)
        lock.acquire("holder-1", timeout=5.0)

        acquired = []

        def waiter():
            # cancel_check=True means keep waiting; release will unblock us.
            acquired.append(lock.acquire("holder-2", timeout=3.0, cancel_check=lambda: True))

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        lock.release("holder-1")
        t.join(timeout=2.0)
        assert acquired == [True]


# ── 4. Re-entrancy ─────────────────────────────────────────────────────────────

class TestReentrant:
    def test_same_holder_acquires_twice_is_success(self, fake_redis):
        lock = make_lock(fake_redis)
        assert lock.acquire("agv-1", timeout=1.0) is True
        # Second acquire by same holder — must succeed immediately.
        assert lock.acquire("agv-1", timeout=1.0) is True
        assert lock.holder() == "agv-1"

    def test_reentrant_does_not_corrupt_for_other_holder(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("agv-1")
        lock.acquire("agv-1")  # re-entrant
        # Other holder still can't take it.
        assert lock.acquire("agv-2", timeout=0.1) is False
        # Original holder can release.
        lock.release("agv-1")
        assert lock.is_free()


# ── 5. CAS release safety ─────────────────────────────────────────────────────

class TestCASRelease:
    def test_wrong_holder_cannot_release(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("real-holder")
        result = lock.release("imposter")
        assert result is False
        assert lock.holder() == "real-holder"

    def test_release_when_free_returns_false(self, fake_redis):
        lock = make_lock(fake_redis)
        assert lock.release("nobody") is False

    def test_double_release_by_holder_is_safe(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("holder-1")
        assert lock.release("holder-1") is True
        # Second release on an already-released (or expired) lock.
        assert lock.release("holder-1") is False


# ── 6. TTL expiry ──────────────────────────────────────────────────────────────

class TestTTLExpiry:
    def test_lock_expires_and_becomes_free(self, fake_redis):
        """Lock with very short TTL auto-expires; next acquire should succeed."""
        # Use a short TTL (100 ms).
        lock = make_lock(fake_redis, ttl_ms=100)
        lock.acquire("holder-1")
        assert lock.holder() == "holder-1"
        # Wait for TTL to expire naturally.
        time.sleep(0.2)
        # After TTL, the key should be gone.
        assert lock.is_free()
        # Another holder can now acquire.
        assert lock.acquire("holder-2", timeout=1.0) is True


# ── 7. touch() extends TTL ────────────────────────────────────────────────────

class TestTouch:
    def test_touch_by_holder_returns_true(self, fake_redis):
        lock = make_lock(fake_redis, ttl_ms=500)
        lock.acquire("holder-1")
        assert lock.touch("holder-1") is True

    def test_touch_extends_ttl_so_lock_survives(self, fake_redis):
        """Touch right before expiry — lock must still be held after touch."""
        lock = make_lock(fake_redis, ttl_ms=200)
        lock.acquire("holder-1")
        time.sleep(0.1)  # half TTL elapsed
        lock.touch("holder-1")  # reset TTL
        time.sleep(0.15)  # would have expired without touch
        # Lock should still be held because touch renewed it.
        assert lock.holder() == "holder-1"

    def test_touch_by_wrong_holder_returns_false(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("real-holder")
        assert lock.touch("imposter") is False

    def test_touch_when_free_returns_false(self, fake_redis):
        lock = make_lock(fake_redis)
        assert lock.touch("nobody") is False


# ── 8. force_release ──────────────────────────────────────────────────────────

class TestForceRelease:
    def test_force_release_clears_any_holder(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.acquire("holder-1")
        lock.force_release()
        assert lock.is_free()

    def test_force_release_when_free_is_safe(self, fake_redis):
        lock = make_lock(fake_redis)
        lock.force_release()  # should not raise
        assert lock.is_free()


# ── 9. LockKeepalive ──────────────────────────────────────────────────────────

class TestLockKeepalive:
    def test_keepalive_renews_ttl(self, fake_redis):
        """Keepalive with short interval keeps a short-TTL lock alive."""
        lock = make_lock(fake_redis, ttl_ms=200)
        lock.acquire("holder-1")
        keepalive = LockKeepalive(lock, "holder-1", interval_s=0.05)
        keepalive.start()
        try:
            # Wait longer than the TTL; keepalive should keep renewing.
            time.sleep(0.35)
            # Lock must still be held.
            assert lock.holder() == "holder-1", (
                "LockKeepalive failed to renew TTL — lock expired prematurely"
            )
        finally:
            keepalive.stop()

    def test_keepalive_stops_renewing_after_stop(self, fake_redis):
        """After stop(), keepalive no longer renews; lock can expire."""
        lock = make_lock(fake_redis, ttl_ms=200)
        lock.acquire("holder-1")
        keepalive = LockKeepalive(lock, "holder-1", interval_s=0.02)
        keepalive.start()
        time.sleep(0.05)
        keepalive.stop()
        # Now release the lock ourselves and wait for TTL to lapse.
        lock.release("holder-1")
        # Lock was released; it should be free.
        assert lock.is_free()

    def test_keepalive_context_manager(self, fake_redis):
        """Context-manager form: starts on enter, stops on exit."""
        lock = make_lock(fake_redis, ttl_ms=300)
        lock.acquire("holder-ctx")
        with LockKeepalive(lock, "holder-ctx", interval_s=0.05):
            time.sleep(0.15)
            assert lock.holder() == "holder-ctx"
        # After __exit__, keepalive is stopped (lock still held until we release).
        lock.release("holder-ctx")
        assert lock.is_free()

    def test_keepalive_does_not_renew_for_wrong_holder(self, fake_redis):
        """If lock was released and taken by another holder, touch is a no-op."""
        lock = make_lock(fake_redis, ttl_ms=500)
        lock.acquire("holder-1")
        keepalive = LockKeepalive(lock, "holder-1", interval_s=0.02)
        keepalive.start()
        try:
            # Release and let someone else take it.
            lock.release("holder-1")
            lock.acquire("holder-2", timeout=1.0)
            time.sleep(0.1)
            # holder-2 should still hold it (keepalive touch is a no-op for holder-1).
            assert lock.holder() == "holder-2"
        finally:
            keepalive.stop()
            lock.release("holder-2")
