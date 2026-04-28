"""Tests for arm_routes — REST API integration tests."""

import json


class TestSetAngle:
    """Tests for POST /set_angle endpoint."""

    def test_set_angle_valid_returns_200(self, client):
        """Valid set_angle request should return 200 with status ok."""
        resp = client.post('/set_angle',
                           data=json.dumps({"arm_id": 1, "angle": 45.0}),
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["arm_id"] == 1
        assert data["angle"] == 45.0

    def test_set_angle_missing_arm_id_returns_400(self, client):
        """Missing arm_id should return 400."""
        resp = client.post('/set_angle',
                           data=json.dumps({"angle": 45.0}),
                           content_type='application/json')
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
        assert "code" in data

    def test_set_angle_missing_angle_returns_400(self, client):
        """Missing angle should return 400."""
        resp = client.post('/set_angle',
                           data=json.dumps({"arm_id": 1}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_set_angle_invalid_range_returns_400(self, client):
        """Angle out of range should return 400."""
        resp = client.post('/set_angle',
                           data=json.dumps({"arm_id": 1, "angle": 999}),
                           content_type='application/json')
        assert resp.status_code == 400
        assert "out of range" in resp.get_json()["error"]

    def test_set_angle_invalid_arm_id_returns_400(self, client):
        """Invalid arm_id (beyond configured count) should return 400."""
        from app.models.pipeline_config import get_arm_count
        invalid_id = get_arm_count() + 100
        resp = client.post('/set_angle',
                           data=json.dumps({"arm_id": invalid_id, "angle": 45}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_set_angle_no_json_body_returns_400(self, client):
        """Request without JSON body should return 400."""
        resp = client.post('/set_angle', data='not json')
        assert resp.status_code == 400


class TestGetState:
    """Tests for GET /state and GET /state/<id> endpoints."""

    def test_get_state_returns_all_arms(self, client):
        """GET /state should return all configured arms."""
        from app.models.pipeline_config import get_arm_count
        resp = client.get('/state')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["arms"]) == get_arm_count()

    def test_get_single_arm_state(self, client):
        """GET /state/1 should return arm 1's state."""
        resp = client.get('/state/1')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["arm_id"] == 1

    def test_get_single_arm_invalid_id(self, client):
        """GET /state/99 should return 400."""
        resp = client.get('/state/99')
        assert resp.status_code == 400


class TestGetConfig:
    """Tests for GET /config endpoint."""

    def test_get_config_returns_required_keys(self, client):
        """GET /config should return all required scene configuration keys."""
        resp = client.get('/config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert "canvas_width" in data
        assert "canvas_height" in data
        assert "arms" in data
        assert "reach_zones" in data
        assert "handoff_zones" in data
        assert "intake_zone" in data
        assert "output_zone" in data
        assert "labels" in data

    def test_get_config_arm_count_matches_config(self, client):
        """Config arm count should match pipeline.yaml."""
        from app.models.pipeline_config import get_arm_count
        resp = client.get('/config')
        data = resp.get_json()
        assert len(data["arms"]) == get_arm_count()

    def test_get_config_handoff_zones_match_arms(self, client):
        """Handoff zones should be N-1 for N arms."""
        resp = client.get('/config')
        data = resp.get_json()
        assert len(data["handoff_zones"]) == len(data["arms"]) - 1


class TestReloadRestartSeparation:
    """Verify reload_config and restart_pipeline are independent operations."""

    def test_reload_does_not_mutate_state(self, client):
        """POST /reload_config reloads YAML but must not reset arm angles."""
        from app.models.pipeline_config import get_arm_ids
        first_arm = get_arm_ids()[0]

        # Move an arm to a non-default angle
        client.post('/set_angle',
                    data=json.dumps({"arm_id": first_arm, "angle": 42.0}),
                    content_type='application/json')
        assert client.get(f'/state/{first_arm}').get_json()["angle"] == 42.0

        # Reload config
        resp = client.post('/reload_config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "arms" in data
        assert "graph_valid" in data

        # Arm angle must be untouched — reload is NOT a state mutation
        assert client.get(f'/state/{first_arm}').get_json()["angle"] == 42.0

    def test_restart_pipeline_resets_state(self, client):
        """POST /restart_pipeline returns arms to DEFAULT_ANGLE with idle status."""
        from app.models.pipeline_config import get_arm_ids, DEFAULT_ANGLE
        first_arm = get_arm_ids()[0]

        # Move an arm off default
        client.post('/set_angle',
                    data=json.dumps({"arm_id": first_arm, "angle": 17.0}),
                    content_type='application/json')
        assert client.get(f'/state/{first_arm}').get_json()["angle"] == 17.0

        # Restart pipeline
        resp = client.post('/restart_pipeline')
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

        # All arms back to defaults
        state = client.get('/state').get_json()
        for arm in state["arms"]:
            assert arm["angle"] == DEFAULT_ANGLE
            assert arm["status"] == "idle"


class TestObjectOwnershipEvents:
    def test_object_event_persists_owner_fields(self, client):
        resp = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjX",
                "state": "carried",
                "carrier_arm": 1,
                "owner_id": "arm-1",
                "owner_type": "arm",
                "event_time": 1000,
            }),
            content_type='application/json',
        )
        assert resp.status_code == 200
        objects = client.get('/objects').get_json()["objects"]
        rec = next(o for o in objects if o["object_id"] == "ObjX")
        assert rec["owner_id"] == "arm-1"
        assert rec["owner_type"] == "arm"
        assert rec["event_time"] == 1000
        assert isinstance(rec.get("event_seq"), int)

    def test_stale_object_event_is_ignored(self, client):
        from app.models import pipeline_config
        flow = pipeline_config.get_module_flow_order()
        first_arm = next(sid for sid in flow if sid.startswith("arm-"))
        next_owner = flow[flow.index(first_arm) + 1]
        tp_id = f"TP {first_arm}->{next_owner}"

        newer = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjY",
                "state": "carried",
                "carrier_arm": int(first_arm.split("-")[1]),
                "owner_id": first_arm,
                "owner_type": "arm",
                "event_time": 2000,
            }),
            content_type='application/json',
        )
        assert newer.status_code == 200

        stale = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjY",
                "state": "transfer_point",
                "owner_id": tp_id,
                "owner_type": "transfer_point",
                "event_time": 1500,
            }),
            content_type='application/json',
        )
        assert stale.status_code == 202
        assert stale.get_json()["status"] == "ignored_stale"

        objects = client.get('/objects').get_json()["objects"]
        rec = next(o for o in objects if o["object_id"] == "ObjY")
        assert rec["state"] == "carried"
        assert rec["owner_id"] == first_arm

    def test_event_sequence_monotonic_for_same_object(self, client):
        from app.models import pipeline_config
        first_arm = next(
            sid for sid in pipeline_config.get_module_flow_order()
            if sid.startswith("arm-")
        )
        first = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjZ",
                "state": "intake",
                "event_time": 3000,
            }),
            content_type='application/json',
        )
        assert first.status_code == 200
        seq1 = next(o for o in client.get('/objects').get_json()["objects"]
                    if o["object_id"] == "ObjZ")["event_seq"]

        second = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjZ",
                "state": "carried",
                "carrier_arm": int(first_arm.split("-")[1]),
                "owner_id": first_arm,
                "owner_type": "arm",
                "event_time": 3001,
            }),
            content_type='application/json',
        )
        assert second.status_code == 200
        seq2 = next(o for o in client.get('/objects').get_json()["objects"]
                    if o["object_id"] == "ObjZ")["event_seq"]
        assert seq2 > seq1

    def test_non_terminal_arm_cannot_transition_to_output(self, client):
        from app.models import pipeline_config
        flow = pipeline_config.get_module_flow_order()
        arm_ids = [sid for sid in flow if sid.startswith("arm-")]
        assert len(arm_ids) >= 2
        non_terminal = arm_ids[0]

        intake = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjW",
                "state": "intake",
                "event_time": 4000,
            }),
            content_type='application/json',
        )
        assert intake.status_code == 200

        carried = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjW",
                "state": "carried",
                "carrier_arm": int(non_terminal.split("-")[1]),
                "owner_id": non_terminal,
                "owner_type": "arm",
                "event_time": 4001,
            }),
            content_type='application/json',
        )
        assert carried.status_code == 200

        invalid = client.post(
            '/object_event',
            data=json.dumps({
                "object_id": "ObjW",
                "state": "output",
                "owner_id": "output-zone",
                "owner_type": "zone",
                "event_time": 4002,
            }),
            content_type='application/json',
        )
        assert invalid.status_code == 409
        assert invalid.get_json()["code"] == "INVALID_OWNERSHIP_TRANSITION"


class TestAssetRecoverRoutes:
    def test_recover_idle_asset_is_noop(self, client, monkeypatch):
        from app.models import asset_state
        from app.services import container_manager

        asset_state.reset()
        asset_state.register("veh-test", "vehicle", "Vehicle Test")

        calls = []
        monkeypatch.setattr(container_manager, "start_container",
                            lambda module_id: calls.append(module_id))

        resp = client.post('/assets/veh-test/recover')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["container_control"] == "not needed"
        assert data["asset"]["status"] == "idle"
        assert calls == []
