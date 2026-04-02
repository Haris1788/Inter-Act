"""Tests for concurrent arm state access — thread safety verification."""

import threading
import json


def test_concurrent_writes_no_corruption(client):
    """Multiple threads writing to different arms should not corrupt state."""
    errors = []

    def set_arm(arm_id, angle):
        try:
            resp = client.post('/set_angle',
                               data=json.dumps({"arm_id": arm_id, "angle": angle}),
                               content_type='application/json')
            if resp.status_code != 200:
                errors.append(f"arm {arm_id} angle {angle}: status {resp.status_code}")
        except Exception as e:
            errors.append(str(e))

    threads = []
    # Send 10 commands to each arm concurrently
    for _ in range(10):
        for arm_id, angle in [(1, 30.0), (2, 120.0), (3, 60.0)]:
            t = threading.Thread(target=set_arm, args=(arm_id, angle))
            threads.append(t)
            t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent errors: {errors}"

    # Verify final state is correct
    resp = client.get('/state')
    data = resp.get_json()
    arms = {a["arm_id"]: a["angle"] for a in data["arms"]}
    assert arms[1] == 30.0
    assert arms[2] == 120.0
    assert arms[3] == 60.0
