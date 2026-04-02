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
