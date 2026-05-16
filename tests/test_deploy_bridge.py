"""Phase 15 deploy bridge - honest topology application."""

import json
import os

from app.services import deploy_bridge


def _payload():
    return {
        "actors": [
            # Deliberately out of x-order to prove sorting drives the chain.
            {"id": "arm-5", "kind": "arm", "label": "Arm 5",
             "position": {"x": 900, "y": 100}},
            {"id": "arm-1", "kind": "arm", "label": "Source",
             "position": {"x": 100, "y": 100}},
            {"id": "veh-a", "kind": "agv", "label": "Shuttle A",
             "position": {"x": 500, "y": 400}},
        ],
        "zones": [],
        "goal": {"text": "", "constraints": {}},
    }


class TestBuildPipelineYaml:
    def test_arms_sorted_into_dependency_chain(self):
        doc = deploy_bridge.build_pipeline_yaml(_payload())
        ids = [a["id"] for a in doc["arms"]]
        assert ids == [1, 5]  # sorted by authored x, not insertion order
        assert doc["arms"][0]["depends_on"] == []
        assert doc["arms"][1]["depends_on"] == [1]
        assert all(a["critical"] for a in doc["arms"])

    def test_vehicle_wired_first_to_last_arm(self):
        doc = deploy_bridge.build_pipeline_yaml(_payload())
        assert len(doc["assets"]) == 1
        veh = doc["assets"][0]
        assert veh["id"] == "veh-a"
        assert veh["type"] == "vehicle"
        assert veh["upstream"] == ["arm-1"]
        assert veh["downstream"] == ["arm-5"]

    def test_preserves_run_policy_blocks(self):
        doc = deploy_bridge.build_pipeline_yaml(_payload())
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
        assert "skipped" in result["steps"]["compose_up"]
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


class TestCreateArmContainerHonest:
    def test_honest_when_docker_unavailable(self, monkeypatch):
        from app.services import container_manager

        monkeypatch.setattr(
            container_manager, "_docker_available", lambda: False
        )
        assert "unavailable" in (container_manager.create_arm_container(5) or "")
        assert "unavailable" in (
            container_manager.create_asset_container("veh-9") or ""
        )
        # And it must never raise / never fake success.
        assert container_manager.container_exists("arm-5") is False


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
            lambda payload: {
                "status": "ok",
                "applied": False,
                "arms": ["arm-1"],
                "assets": [],
                "steps": {"config_written": "ok"},
                "detail": "stubbed - no containers started",
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
        assert body["arms"] == ["arm-1"]
