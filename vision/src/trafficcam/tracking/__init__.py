"""Tracking: turns per-frame detections into tracks with stable ids."""

from datetime import datetime
from typing import Protocol

import supervision as sv


class Tracker(Protocol):
    def update(self, detections: sv.Detections, timestamp: datetime) -> sv.Detections:
        """Return the confirmed tracks in this frame, each with a `tracker_id`."""
        ...
