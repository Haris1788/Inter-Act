from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from event_types import EventType


@dataclass
class Event:
    """Unified event structure for Phase 1 message bus."""

    type: EventType
    sender: str
    task_id: str
    payload: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
