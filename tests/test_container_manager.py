"""Tests for container_manager — actor reconciler and label ownership model.

Focused assertions on the behaviour Track C adds:
  - desired set includes AGVs when pipeline_config.get_agvs() is non-empty
  - create_actor normalises "vehicle" and "agv" kinds to "agv"
  - consolidated image (_ACTOR_IMAGE) is used for all kinds
  - ensure_shared_image() is kind-agnostic (single image)
  - reconcile_actors is honest when docker is unavailable (regression guard)
  - build_desired_from_config includes both arms and AGVs
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline_config(arm_ids, agv_ids):
    """Return a minimal pipeline_config mock with specified arm/agv ids."""

    class _Arm:
        def __init__(self, n):
            self._d = {"id": n, "label": f"Arm {n}"}

        def __getitem__(self, k):
            return self._d[k]

        def get(self, k, default=None):
            return self._d.get(k, default)

    class _Agv:
        def __init__(self, aid):
            self._d = {"id": aid, "label": aid}

        def __getitem__(self, k):
            return self._d[k]

        def get(self, k, default=None):
            return self._d.get(k, default)

    arms = [_Arm(n) for n in arm_ids]
    agvs = [_Agv(aid) for aid in agv_ids]

    class _PC:
        @staticmethod
        def get_arms():
            return arms

        @staticmethod
        def get_agvs():
            return agvs

    return _PC()


# ---------------------------------------------------------------------------
# Image consolidation
# ---------------------------------------------------------------------------

class TestImageConsolidation:
    def test_single_actor_image_constant(self):
        from app.services import container_manager as cm

        assert cm._ACTOR_IMAGE == "interact-shared-actor:latest"

    def test_legacy_images_in_shared_set(self):
        """Legacy arm/asset image names must be in _SHARED_IMAGE_SET so the
        reclaim path still removes old containers."""
        from app.services import container_manager as cm

        assert "interact-shared-arm:latest" in cm._SHARED_IMAGE_SET
        assert "interact-shared-asset:latest" in cm._SHARED_IMAGE_SET
        assert cm._ACTOR_IMAGE in cm._SHARED_IMAGE_SET

    def test_ensure_shared_image_is_kind_agnostic(self, monkeypatch):
        """ensure_shared_image() accepts any kind string and always targets
        the single consolidated actor image."""
        from app.services import container_manager as cm

        calls = []

        def fake_image_exists(tag):
            calls.append(tag)
            return True  # pretend already built

        monkeypatch.setattr(cm, "_image_exists", fake_image_exists)
        result = cm.ensure_shared_image("arm")
        assert result is None
        assert cm._ACTOR_IMAGE in calls

        calls.clear()
        result = cm.ensure_shared_image("agv")
        assert result is None
        assert cm._ACTOR_IMAGE in calls

        calls.clear()
        result = cm.ensure_shared_image()
        assert result is None
        assert cm._ACTOR_IMAGE in calls


# ---------------------------------------------------------------------------
# Kind normalisation in create_actor
# ---------------------------------------------------------------------------

class TestCreateActorKindNormalisation:
    def test_docker_unavailable_returns_honest_message(self, monkeypatch):
        from app.services import container_manager as cm

        monkeypatch.setattr(cm, "_docker_available", lambda: False)
        assert "unavailable" in (cm.create_actor("arm-1", "arm", "Arm 1", None) or "")
        assert "unavailable" in (cm.create_actor("agv-1", "agv", "AGV 1", None) or "")
        assert "unavailable" in (cm.create_actor("veh-a", "vehicle", "Veh A", None) or "")

    def test_agv_and_vehicle_map_to_agv_kind(self, monkeypatch):
        """Ensure "vehicle" is normalised to "agv" so the actor_runner.py
        receives ACTOR_KIND=agv, and legacy callers are backward-compatible."""
        from app.services import container_manager as cm

        captured: list[list[str]] = []

        # Docker is "available" but we intercept the run call.
        monkeypatch.setattr(cm, "_docker_available", lambda: True)
        monkeypatch.setattr(cm, "_image_exists", lambda tag: True)
        monkeypatch.setattr(cm, "_infra_runtime", lambda: {
            "network": "interact_default",
            "pipeline_mount": None,
            "project": None,
        })

        def fake_run(args, **kw):
            captured.append(list(args))

            class R:
                returncode = 0
                stderr = ""
                stdout = ""

            return R()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        # kind="vehicle"
        cm.create_actor("veh-a", "vehicle", "Shuttle A", None)
        env_args = " ".join(captured[-1])
        assert "ACTOR_KIND=agv" in env_args
        assert "ACTOR_KIND=vehicle" not in env_args

        # kind="agv"
        captured.clear()
        cm.create_actor("agv-1", "agv", "AGV 1", None)
        env_args = " ".join(captured[-1])
        assert "ACTOR_KIND=agv" in env_args

    def test_arm_kind_preserved(self, monkeypatch):
        from app.services import container_manager as cm

        captured: list[list[str]] = []

        monkeypatch.setattr(cm, "_docker_available", lambda: True)
        monkeypatch.setattr(cm, "_image_exists", lambda tag: True)
        monkeypatch.setattr(cm, "_infra_runtime", lambda: {
            "network": "interact_default",
            "pipeline_mount": None,
            "project": None,
        })

        def fake_run(args, **kw):
            captured.append(list(args))

            class R:
                returncode = 0
                stderr = ""
                stdout = ""

            return R()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        cm.create_actor("arm-1", "arm", "Arm 1", None)
        env_args = " ".join(captured[-1])
        assert "ACTOR_KIND=arm" in env_args
        assert "ACTOR_ID=arm-1" in env_args
        assert "INTERACT_BUS=redis" in env_args
        assert "INTERACT_STATE=redis" in env_args
        # All kinds use the consolidated actor image.
        assert cm._ACTOR_IMAGE in captured[-1]


# ---------------------------------------------------------------------------
# build_desired_from_config — includes both arms and AGVs
# ---------------------------------------------------------------------------

class TestBuildDesiredFromConfig:
    def test_includes_arms_and_agvs(self, monkeypatch):
        from app.services import container_manager as cm
        from app.models import pipeline_config as pc_mod

        mock_pc = _make_pipeline_config(arm_ids=[1, 2], agv_ids=["agv-1", "agv-2"])
        monkeypatch.setattr(pc_mod, "get_arms", mock_pc.get_arms)
        monkeypatch.setattr(pc_mod, "get_agvs", mock_pc.get_agvs)

        desired = cm.build_desired_from_config()
        ids = [d["id"] for d in desired]
        kinds = {d["id"]: d["kind"] for d in desired}

        assert "arm-1" in ids
        assert "arm-2" in ids
        assert "agv-1" in ids
        assert "agv-2" in ids

        assert kinds["arm-1"] == "arm"
        assert kinds["arm-2"] == "arm"
        assert kinds["agv-1"] == "agv"
        assert kinds["agv-2"] == "agv"

    def test_no_agvs_returns_only_arms(self, monkeypatch):
        from app.services import container_manager as cm
        from app.models import pipeline_config as pc_mod

        mock_pc = _make_pipeline_config(arm_ids=[1, 3], agv_ids=[])
        monkeypatch.setattr(pc_mod, "get_arms", mock_pc.get_arms)
        monkeypatch.setattr(pc_mod, "get_agvs", mock_pc.get_agvs)

        desired = cm.build_desired_from_config()
        assert all(d["kind"] == "arm" for d in desired)
        assert len(desired) == 2

    def test_arm_ids_are_prefixed(self, monkeypatch):
        from app.services import container_manager as cm
        from app.models import pipeline_config as pc_mod

        mock_pc = _make_pipeline_config(arm_ids=[7], agv_ids=[])
        monkeypatch.setattr(pc_mod, "get_arms", mock_pc.get_arms)
        monkeypatch.setattr(pc_mod, "get_agvs", mock_pc.get_agvs)

        desired = cm.build_desired_from_config()
        assert desired[0]["id"] == "arm-7"

    def test_agv_ids_are_passed_through(self, monkeypatch):
        """AGV ids come from pipeline_config verbatim (no prefix added)."""
        from app.services import container_manager as cm
        from app.models import pipeline_config as pc_mod

        mock_pc = _make_pipeline_config(arm_ids=[], agv_ids=["agv-1", "agv-42"])
        monkeypatch.setattr(pc_mod, "get_arms", mock_pc.get_arms)
        monkeypatch.setattr(pc_mod, "get_agvs", mock_pc.get_agvs)

        desired = cm.build_desired_from_config()
        assert {d["id"] for d in desired} == {"agv-1", "agv-42"}


# ---------------------------------------------------------------------------
# reconcile_actors — AGV and arm honest no-op (regression guard)
# ---------------------------------------------------------------------------

class TestReconcileActorsWithAgvs:
    def test_reconcile_honest_when_docker_unavailable(self, monkeypatch):
        from app.services import container_manager as cm

        monkeypatch.setattr(cm, "_docker_available", lambda: False)
        res = cm.reconcile_actors([
            {"id": "arm-1", "kind": "arm", "label": "Source"},
            {"id": "agv-1", "kind": "agv", "label": "AGV 1"},
        ])
        assert res["applied"] is False
        assert res["created"] == []
        assert res["removed"] == []
        assert "honest" in res["detail"]

    def test_list_actor_containers_safe_no_docker(self, monkeypatch):
        from app.services import container_manager as cm

        monkeypatch.setattr(cm, "_docker_available", lambda: False)
        result = cm.list_actor_containers()
        assert result == {"owned": {}, "legacy": []}


# ---------------------------------------------------------------------------
# Ownership labels
# ---------------------------------------------------------------------------

class TestOwnershipLabels:
    def test_label_constants(self):
        from app.services import container_manager as cm

        assert cm.LBL_ROLE == "interact.role"
        assert cm.LBL_ID == "interact.actor.id"
        assert cm.LBL_KIND == "interact.actor.kind"
        assert cm.ROLE_ACTOR == "actor"

    def test_core_modules_excluded(self):
        from app.services import container_manager as cm

        assert "backend" in cm._CORE_MODULES
        assert "frontend" in cm._CORE_MODULES
