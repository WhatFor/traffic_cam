"""Speeding: a passage whose sustained speed is well over the limit."""

import uuid
from datetime import datetime

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage
from trafficcam.geometry import Observation
from trafficcam.speed import KMH_PER_MPH

EVENT_TYPE = "speeding"
DETECTOR_VERSION = "1"


class Speeding:
    """Looks at each passage's speed when it closes.

    The event is dated from the middle of the second in which the speed was held, so a
    clip of it shows the vehicle at its fastest.
    """

    def __init__(self, config: SiteConfig, config_hash: str) -> None:
        settings = config.detectors.speed
        if settings is None:
            raise ValueError("detectors.speed is not configured")
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._limit_mph = settings.limit_mph
        self._flag_above_mph = settings.flag_above_mph

    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        return []

    def passage_closed(self, passage: Passage) -> list[Event]:
        if passage.speed_kmh is None:
            return []
        speed_mph = passage.speed_kmh / KMH_PER_MPH
        if speed_mph <= self._flag_above_mph:
            return []
        held_at = passage.flags.get("speed", {}).get("sustained_at")
        event = Event.model_validate(
            {
                # Derived from the passage, so a replay gives the same id.
                "id": uuid.uuid5(passage.id, EVENT_TYPE),
                "ts": held_at or passage.last_seen,
                "camera": self._camera,
                "config_hash": self._config_hash,
                "type": EVENT_TYPE,
                "detector_version": DETECTOR_VERSION,
                "passage_id": passage.id,
                "track_id": passage.track_id,
                "class": passage.class_,
                "confidence": None,
                "attrs": {
                    "speed_mph": round(speed_mph, 1),
                    "speed_kmh": passage.speed_kmh,
                    "limit_mph": self._limit_mph,
                    "movement": passage.movement,
                },
                "clip_id": None,
            }
        )
        return [event]
