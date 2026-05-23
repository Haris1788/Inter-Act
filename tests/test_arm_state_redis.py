"""arm_state contract tests — parametrized over inmemory and Redis backends.

Pattern mirrors test_bus_contract.py.  Redis backend uses fakeredis.
"""

from __future__ import annotations

import fakeredis
import pytest

from app.models.pipeline_config import get_arm_ids, get_arm_count, DEFAULT_ANGLE

ARM_IDS = get_arm_ids()
FIRST_ARM = ARM_IDS[0]
LAST_ARM = ARM_IDS[-1]


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def inmemory_backend(monkeypatch):
    monkeypatch.setenv("INTERACT_STATE", "inmemory")
    import app.models.arm_state as arm
    arm.reset()
    yield
    arm.reset()


@pytest.fixture()
def redis_backend(monkeypatch):
    monkeypatch.setenv("INTERACT_STATE", "redis")
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    import app.models.arm_state as arm
    arm._inject_redis_client(client)
    # Force re-initialization from config into Redis.
    arm.reinitialize()
    yield
    arm.reset()


@pytest.fixture(params=["inmemory", "redis"])
def backend(request, inmemory_backend, redis_backend):
    return request.param


# ── Contract tests ─────────────────────────────────────────────────────────────

class TestInitialState:
    def test_all_arms_at_default_angle(self, backend):
        import app.models.arm_state as arm
        state = arm.get_all()
        assert len(state["arms"]) == get_arm_count()
        for a in state["arms"]:
            assert a["angle"] == float(DEFAULT_ANGLE)

    def test_initial_status_is_idle(self, backend):
        import app.models.arm_state as arm
        for arm_id in ARM_IDS:
            s = arm.get_state(arm_id)
            assert s["status"] == "idle"


class TestSetAngle:
    def test_set_valid_angle(self, backend):
        import app.models.arm_state as arm
        assert arm.set_angle(FIRST_ARM, 45.0) is True
        assert arm.get_state(FIRST_ARM)["angle"] == 45.0

    def test_set_boundary_zero(self, backend):
        import app.models.arm_state as arm
        arm.set_angle(FIRST_ARM, 0.0)
        assert arm.get_state(FIRST_ARM)["angle"] == 0.0

    def test_set_boundary_180(self, backend):
        import app.models.arm_state as arm
        arm.set_angle(FIRST_ARM, 180.0)
        assert arm.get_state(FIRST_ARM)["angle"] == 180.0

    def test_integer_angle_stored_as_float(self, backend):
        import app.models.arm_state as arm
        arm.set_angle(FIRST_ARM, 45)
        assert arm.get_state(FIRST_ARM)["angle"] == 45.0

    def test_reject_negative_angle(self, backend):
        import app.models.arm_state as arm
        with pytest.raises(ValueError, match="out of range"):
            arm.set_angle(FIRST_ARM, -1.0)

    def test_reject_angle_above_180(self, backend):
        import app.models.arm_state as arm
        with pytest.raises(ValueError, match="out of range"):
            arm.set_angle(FIRST_ARM, 181.0)

    def test_reject_non_numeric_angle(self, backend):
        import app.models.arm_state as arm
        with pytest.raises(ValueError, match="must be a number"):
            arm.set_angle(FIRST_ARM, "bad")

    def test_reject_invalid_arm_id_zero(self, backend):
        import app.models.arm_state as arm
        with pytest.raises(ValueError, match="Invalid arm_id"):
            arm.set_angle(0, 45.0)

    def test_reject_arm_id_beyond_config(self, backend):
        import app.models.arm_state as arm
        invalid = max(ARM_IDS) + 100
        with pytest.raises(ValueError, match="Invalid arm_id"):
            arm.set_angle(invalid, 45.0)

    def test_set_one_arm_does_not_affect_others(self, backend):
        import app.models.arm_state as arm
        for arm_id in ARM_IDS:
            arm.set_angle(arm_id, 90.0)
        arm.set_angle(FIRST_ARM, 30.0)
        for arm_id in ARM_IDS:
            if arm_id == FIRST_ARM:
                assert arm.get_state(arm_id)["angle"] == 30.0
            else:
                assert arm.get_state(arm_id)["angle"] == 90.0


class TestGetState:
    def test_get_state_returns_dict(self, backend):
        import app.models.arm_state as arm
        state = arm.get_state(FIRST_ARM)
        assert "arm_id" in state
        assert "angle" in state
        assert "status" in state

    def test_get_all_count(self, backend):
        import app.models.arm_state as arm
        state = arm.get_all()
        assert len(state["arms"]) == get_arm_count()

    def test_get_state_invalid_id_raises(self, backend):
        import app.models.arm_state as arm
        with pytest.raises(ValueError, match="Invalid arm_id"):
            arm.get_state(max(ARM_IDS) + 999)


class TestHealth:
    def test_set_and_get_health(self, backend):
        import app.models.arm_state as arm
        arm.set_health(FIRST_ARM, "alive")
        h = arm.get_health(FIRST_ARM)
        assert h["health"] == "alive"

    def test_set_health_with_error(self, backend):
        import app.models.arm_state as arm
        arm.set_health(FIRST_ARM, "dead", error="connection lost")
        h = arm.get_health(FIRST_ARM)
        assert h["health"] == "dead"
        assert h["error"] == "connection lost"

    def test_get_all_health_returns_all_arms(self, backend):
        import app.models.arm_state as arm
        all_h = arm.get_all_health()
        for arm_id in ARM_IDS:
            assert arm_id in all_h


class TestReset:
    def test_reset_restores_default_angle(self, backend):
        import app.models.arm_state as arm
        arm.set_angle(FIRST_ARM, 45.0)
        arm.reset()
        assert arm.get_state(FIRST_ARM)["angle"] == float(DEFAULT_ANGLE)

    def test_reset_clears_pipeline_stopped(self, backend):
        import app.models.arm_state as arm
        arm.mark_arm_stopped(FIRST_ARM)
        arm.reset()
        assert not arm.is_pipeline_stopped()
