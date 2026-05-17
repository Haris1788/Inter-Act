"""Tests for concurrent arm state access - thread safety verification.

Arm IDs and angles are derived from pipeline_config so this test scales
to any arm count defined in pipeline.yaml.
"""

import threading
import json

from app.models.pipeline_config import get_arm_ids


def _deterministic_angles_for_arms(arm_ids):
    """Assign each arm a distinct angle in [0, 180] spaced evenly."""
    n = len(arm_ids)
    if n == 1:
        return [(arm_ids[0], 90.0)]
    step = 180.0 / (n - 1)
    return [(aid, round(i * step, 1)) for i, aid in enumerate(arm_ids)]


def test_concurrent_writes_no_corruption(client):
    """Multiple threads writing to different arms should not corrupt state."""
    errors = []
    arm_angle_pairs = _deterministic_angles_for_arms(get_arm_ids())

    def set_arm(arm_id, angle):
        try:
            resp = client.post(
                "/set_angle",
                data=json.dumps({"arm_id": arm_id, "angle": angle}),
                content_type="application/json",
            )
            if resp.status_code != 200:
                errors.append(f"arm {arm_id} angle {angle}: status {resp.status_code}")
        except Exception as e:
            errors.append(str(e))

    threads = []
    # Send 10 commands to each arm concurrently
    for _ in range(10):
        for arm_id, angle in arm_angle_pairs:
            t = threading.Thread(target=set_arm, args=(arm_id, angle))
            threads.append(t)
            t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent errors: {errors}"

    # Verify final state matches the last write per arm
    resp = client.get("/state")
    data = resp.get_json()
    arms = {a["arm_id"]: a["angle"] for a in data["arms"]}
    for arm_id, angle in arm_angle_pairs:
        assert (
            arms[arm_id] == angle
        ), f"arm {arm_id} expected {angle}, got {arms[arm_id]}"
