"""Arm actuation helper — extracted from dispatch_routes so it can be used by
both the legacy dispatch path and the new ArmAgent._execute_leg (Stage 3).

A single `sweep_arm(arm_id, target_angle, steps, step_sleep)` drives
`arm_state.set_angle` and emits `arm_angle_update` per step.

Angle constants (from CLAUDE.md):
    PICK  = 180  (arm sweeps to pick position)
    PLACE = 0    (arm sweeps to place/deposit position)
    REST  = 90   (arm rests at neutral)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.models import arm_state

logger = logging.getLogger(__name__)

PICK: int = 180
PLACE: int = 0
REST: int = 90

# Default sweep parameters — chosen so each sweep takes ~0.4 s (ACTION_DURATION).
DEFAULT_STEPS: int = 5
DEFAULT_STEP_SLEEP: float = 0.08  # 5 steps × 0.08 s ≈ 0.4 s


def sweep_arm(
    arm_id: int,
    target_angle: int,
    steps: int = DEFAULT_STEPS,
    step_sleep: float = DEFAULT_STEP_SLEEP,
    halt_check: Optional[callable] = None,
) -> bool:
    """Sweep arm `arm_id` to `target_angle` in `steps` increments.

    Between every step, emits `arm_angle_update` via SocketIO (best-effort)
    and calls `arm_state.set_angle`.

    `halt_check` is an optional callable() -> bool. When it returns False
    the sweep stops immediately and returns False (halt).  When halt_check
    is None the sweep always runs to completion and returns True.

    Returns True on completion, False if halted early.
    """
    try:
        current_state = arm_state.get_state(arm_id)
        current_angle = float(current_state.get("angle", REST))
    except (ValueError, KeyError):
        logger.warning("sweep_arm: arm_id=%s not found in arm_state", arm_id)
        current_angle = float(REST)

    steps = max(1, steps)
    delta = (target_angle - current_angle) / steps

    for i in range(1, steps + 1):
        if halt_check is not None and not halt_check():
            logger.info(
                "sweep_arm: halt requested mid-sweep arm=%s target=%s at step %d/%d",
                arm_id, target_angle, i, steps,
            )
            return False

        angle = current_angle + delta * i
        # Clamp to valid range
        angle = max(0.0, min(360.0, angle))

        try:
            arm_state.set_angle(arm_id, angle)
        except (ValueError, KeyError) as exc:
            logger.warning("sweep_arm: set_angle failed: %s", exc)
            return False

        _emit_angle(arm_id, angle)
        time.sleep(step_sleep)

    return True


def _emit_angle(arm_id: int, angle: float) -> None:
    """Best-effort SocketIO emit — never raises."""
    try:
        from app import socketio
        socketio.emit("arm_angle_update", {"arm_id": arm_id, "angle": angle})
    except Exception:
        pass
