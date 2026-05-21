"""Backend re-export of the Phase-2 event taxonomy.

The actual definitions live in scripts/events.py so that AGV and arm
containers (which only COPY scripts/) can import them too. Keep this file
to a re-export only — never duplicate constants here.
"""
from __future__ import annotations

import sys
import pathlib

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from events import (  # noqa: F401  (re-export)
    ACTOR_ONLINE, ACTOR_OFFLINE, CAPABILITY_DECLARED,
    TASK_CREATED, TASK_AVAILABILITY, PATH_PROPOSED,
    PATH_ACCEPTED, PATH_REJECTED, TASK_COMPLETED,
    ACTOR_BUSY, ACTOR_FREE,
    ACTOR_FAILED, REPLAN_REQUIRED,
    make_envelope,
)
