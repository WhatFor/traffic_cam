"""Banned turns: passages that made a movement the site forbids."""

import uuid
from datetime import datetime

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage
from trafficcam.geometry import Observation

EVENT_TYPE = "banned_turn"
DETECTOR_VERSION = "1"


class BannedTurns:
    """Raises one event for each passage whose entry and exit match a listed movement.

    A track that was also seen in some other arm's exit is not believed: that is what a
    track looks like when the tracker has passed one vehicle's id on to another.
    """

    def __init__(self, config: SiteConfig, config_hash: str) -> None:
        settings = config.detectors.banned_turns
        if settings is None:
            raise ValueError("detectors.banned_turns is not configured")
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._banned = {
            (config.movements[name].from_, config.movements[name].to): name
            for name in settings.movements
        }
        self._exit_arms = {
            name: zone.arm for name, zone in config.zones.items() if zone.role == "exit"
        }
        self._arms = {name: zone.arm for name, zone in config.zones.items()}
        self._exits_seen: dict[int, set[str | None]] = {}

    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        ids = observation.tracks.tracker_id
        if ids is None:
            return []
        for name, arm in self._exit_arms.items():
            for track_id in ids[observation.zones[name]].tolist():
                self._exits_seen.setdefault(track_id, set()).add(arm)
        return []

    def passage_closed(self, passage: Passage) -> list[Event]:
        exits_seen = self._exits_seen.pop(passage.track_id or -1, set())
        name = self._banned.get((passage.entry_zone or "", passage.exit_zone or ""))
        if name is None:
            return []
        expected = {self._arms[passage.entry_zone or ""], self._arms[passage.exit_zone or ""]}
        if not exits_seen <= expected:
            return []
        return [
            Event.model_validate(
                {
                    # Derived from the passage, so a replay gives the same id.
                    "id": uuid.uuid5(passage.id, EVENT_TYPE),
                    # When the vehicle came into view, which is when it entered the junction.
                    "ts": passage.first_seen,
                    "camera": self._camera,
                    "config_hash": self._config_hash,
                    "type": EVENT_TYPE,
                    "detector_version": DETECTOR_VERSION,
                    "passage_id": passage.id,
                    "track_id": passage.track_id,
                    "class": passage.class_,
                    "confidence": None,
                    "attrs": {"turn": name, "movement": passage.movement},
                    "clip_id": None,
                }
            )
        ]
