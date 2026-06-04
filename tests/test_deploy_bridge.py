"""Phase 15 deploy bridge - honest topology application."""

import json
import os

from app.services import deploy_bridge


def _payload():
    return {
        "actors": [
            # Deliberately out of x-order to prove sorting drives the chain.
            {"id": "arm-5", "kind": "arm", "label": "Arm 5",
             "position": {"x": 900, "y": 100}, "size": {"w": 210, "h": 150}},
            {"id": "arm-1", "kind": "arm", "label": "Source",
             "position": {"x": 100, "y": 100}, "size": {"w": 210, "h": 150}},
            {"id": "veh-a", "kind": "agv", "label": "Shuttle A",
             "position": {"x": 500, "y": 400}, "size": {"w": 184, "h": 118}},
        ],
        "zones": [],
        "goal": {"text": "", "constraints": {}},
    }


class TestBuildPipelineYaml:
    def test_arms_sorted_into_dependency_chain(self):
        doc, _warnings = deploy_bridge.build_pipeline_yaml(_payload())
        ids = [a["id"] for a in doc["arms"]]
        assert ids == [1, 5]  # sorted by authored x, not insertion order
        assert doc["arms"][0]["depends_on"] == []
        assert doc["arms"][1]["depends_on"] == [1]
        assert all(a["critical"] for a in doc["arms"])

    def test_vehicle_wired_first_to_last_arm(self):
        doc, _warnings = deploy_bridge.build_pipeline_yaml(_payload())
        assert len(doc["assets"]) == 1
        veh = doc["assets"][0]
        assert veh["id"] == "veh-a"
        assert veh["type"] == "vehicle"
        assert veh["upstream"] == ["arm-1"]
        assert veh["downstream"] == ["arm-5"]

    def test_preserves_run_policy_blocks(self):
        doc, _warnings = deploy_bridge.build_pipeline_yaml(_payload())
        for key in ("pipeline", "failure_policy", "health"):
            assert key in doc and doc[key]


class TestApplyTopologyHonest:
    def test_writes_config_but_does_not_fake_containers(self, tmp_path):
        cfg = tmp_path / "pipeline.yaml"
        comp = tmp_path / "docker-compose.yml"

        result = deploy_bridge.apply_topology(
            _payload(),
            config_path=str(cfg),
            compose_path=str(comp),
            run_compose=False,       # docker not exercised in unit test
            reload_runtime=False,    # don't mutate global runtime singletons
        )

        assert result["status"] == "ok"
        # No containers were started → honest false, no fake "ready".
        assert result["applied"] is False
        assert result["arms"] == ["arm-1", "arm-5"]
        assert result["steps"]["config_written"] == "ok"
        assert result["steps"]["compose_generated"] == "ok"
        # Actors are reconciled, not compose-up'd; docker off → honest.
        assert result["steps"]["created"] == "none"
        assert result["steps"]["removed"] == "none"
        assert "honest" in result["detail"]

        # Real artifacts exist on disk.
        assert os.path.exists(cfg) and os.path.exists(comp)
        written = cfg.read_text(encoding="utf-8")
        # Arms keyed by integer id; vehicle wiring references arm-N strings.
        assert "id: 1" in written
        assert "id: 5" in written
        assert "- arm-5" in written  # vehicle downstream wiring

    def test_readonly_config_path_is_reported_not_crashed(self, tmp_path):
        # Point at a directory path → open() for write raises OSError,
        # which the bridge must turn into an honest skip, not a 500.
        result = deploy_bridge.apply_topology(
            _payload(),
            config_path=str(tmp_path),  # a dir, not a file
            compose_path=str(tmp_path / "dc.yml"),
            run_compose=False,
            reload_runtime=False,
        )
        assert result["applied"] is False
        assert "not writable" in result["detail"]
        assert result["steps"].get("compose_generated") is None


class TestUnifiedActorRuntime:
    def test_reconcile_honest_when_docker_unavailable(self, monkeypatch):
        from app.services import container_manager as cm

        monkeypatch.setattr(cm, "_docker_available", lambda: False)
        res = cm.reconcile_actors(
            [{"id": "arm-1", "kind": "arm", "label": "Source"}]
        )
        assert res["applied"] is False
        assert res["created"] == [] and res["removed"] == []
        assert "honest" in res["detail"]
        # list is also a safe no-op (never hits the host docker daemon)
        assert cm.list_actor_containers() == {"owned": {}, "legacy": []}

    def test_create_actor_honest_when_docker_unavailable(self, monkeypatch):
        from app.services import container_manager as cm

        monkeypatch.setattr(cm, "_docker_available", lambda: False)
        assert "unavailable" in (
            cm.create_actor("arm-7", "arm", "Arm 7", 8007) or ""
        )
        assert "unavailable" in (
            cm.create_actor("veh-z", "vehicle", "Veh Z", 8090) or ""
        )

    def test_module_from_name_handles_legacy_and_unified(self):
        from app.services import container_manager as cm

        assert cm._module_from_name("interact-arm-5") == "arm-5"
        # legacy bridge namespace is still reclaimable
        assert cm._module_from_name("interact-dyn-arm-5") == "arm-5"
        assert cm._module_from_name("interact-veh-a") == "veh-a"
        # non-prefixed (host noise) ignored
        assert cm._module_from_name("some-other-container") is None

    def test_ownership_labels_are_explicit(self):
        from app.services import container_manager as cm

        # Ownership is label-driven, not name-driven.
        assert cm.LBL_ROLE == "interact.role"
        assert cm.ROLE_ACTOR == "actor"
        assert "backend" in cm._CORE_MODULES


class TestDeployApplyRoute:
    def test_bad_payload_returns_400(self, client):
        resp = client.post(
            "/deploy/apply",
            data=json.dumps({"zones": []}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "BAD_PAYLOAD"

    def test_route_returns_bridge_result(self, client, monkeypatch):
        # Stub the bridge so the route test never touches the real
        # pipeline.yaml / docker.
        monkeypatch.setattr(
            deploy_bridge,
            "apply_topology",
            lambda payload, **kw: {
                "status": "ok",
                "applied": False,
                "running": True,
                "arms": ["arm-1"],
                "assets": [],
                "steps": {"config_written": "ok"},
                "detail": "stubbed - materializing in background",
            },
        )
        resp = client.post(
            "/deploy/apply",
            data=json.dumps({"actors": [{"id": "arm-1", "kind": "arm"}]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["applied"] is False

    def test_deploy_status_shape(self, client):
        resp = client.get("/deploy/status")
        assert resp.status_code == 200
        body = resp.get_json()
        for key in ("phase", "running", "applied", "detail"):
            assert key in body
