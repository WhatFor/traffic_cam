"""Detectors: turn what the pipeline sees into events."""

from datetime import datetime
from typing import Protocol

from trafficcam.contracts import Event, Passage
from trafficcam.geometry import Observation


class Detector(Protocol):
    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        """Take in one frame. Returns the events it settles."""
        ...

    def passage_closed(self, passage: Passage) -> list[Event]:
        """Called once a track's passage is complete, when its movement is known."""
        ...
