"""Near misses: two road users on crossing paths, a short time apart at the same spot."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage
from trafficcam.detectors.conflicts import Conflict, Conflicts
from trafficcam.geometry import Observation
from trafficcam.speed import MPH_PER_MPS

EVENT_TYPE = "near_miss"
DETECTOR_VERSION = "1"
# A track that never touched a zone has no passage to wait for.
WAIT_FOR_PASSAGES = timedelta(seconds=120)


@dataclass(slots=True)
class _Pending:
    conflict: Conflict
    passages: dict[int, Passage] = field(default_factory=dict)


class NearMiss:
    """Raises one event for each pair of tracks that came within `pet_max_s` of each other.

    The event waits until both passages have closed, so that it can say which movements
    met. Two vehicles that came in from the same arm are not in conflict, whatever their
    headings were at the moment, and raise nothing.
    """

    def __init__(self, config: SiteConfig, config_hash: str, conflicts: Conflicts) -> None:
        settings = config.detectors.near_miss
        ground_map = config.ground_map()
        if settings is None or ground_map is None:
            raise ValueError("detectors.near_miss is not configured")
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._conflicts = conflicts
        self._ground_map = ground_map
        self._pet_max_s = settings.pet_max_s
        self._arm_of = {name: zone.arm for name, zone in config.zones.items()}
        self._pending: dict[frozenset[int], _Pending] = {}

    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        for conflict in self._conflicts.found(observation, timestamp):
            # Kept whatever its gap: the pair is judged where its paths come closest, and
            # a wider gap there outweighs a narrower one a little way off.
            pair = frozenset((conflict.first, conflict.second))
            pending = self._pending.setdefault(pair, _Pending(conflict))
            if conflict.distance_m < pending.conflict.distance_m:
                pending.conflict = conflict
        overdue = [
            pair
            for pair, pending in self._pending.items()
            if timestamp - pending.conflict.at > WAIT_FOR_PASSAGES
        ]
        return [event for pair in overdue for event in self._settle(self._pending.pop(pair))]

    def passage_closed(self, passage: Passage) -> list[Event]:
        events = []
        for pair in [pair for pair in self._pending if passage.track_id in pair]:
            pending = self._pending[pair]
            assert passage.track_id is not None
            pending.passages[passage.track_id] = passage
            if len(pending.passages) == 2:
                events += self._settle(self._pending.pop(pair))
        return events

    def _settle(self, pending: _Pending) -> list[Event]:
        conflict = pending.conflict
        if conflict.gap_s >= self._pet_max_s:
            return []
        first, second = pending.passages.get(conflict.first), pending.passages.get(conflict.second)
        arms = [
            self._arm_of.get(passage.entry_zone) if passage and passage.entry_zone else None
            for passage in (first, second)
        ]
        if arms[0] is not None and arms[0] == arms[1]:
            return []
        ((pixel_x, pixel_y),) = self._ground_map.to_pixels(np.array([[conflict.x, conflict.y]]))
        name = (
            f"trafficcam/{self._camera}/{EVENT_TYPE}/{conflict.at.isoformat()}"
            f"/{conflict.first}/{conflict.second}"
        )
        event = Event.model_validate(
            {
                # Derived from what identifies the meeting, so a replay gives the same id.
                "id": uuid.uuid5(uuid.NAMESPACE_URL, name),
                "ts": conflict.at,
                "camera": self._camera,
                "config_hash": self._config_hash,
                "type": EVENT_TYPE,
                "detector_version": DETECTOR_VERSION,
                # The second to reach the spot: the one that came close to the other.
                "passage_id": second.id if second else None,
                "track_id": conflict.second,
                "class": second.class_ if second else None,
                "confidence": None,
                "attrs": {
                    "pet_s": round(conflict.gap_s, 2),
                    "angle_deg": round(conflict.angle_deg),
                    "speeds_mph": [
                        round(conflict.first_mps * MPH_PER_MPS, 1),
                        round(conflict.second_mps * MPH_PER_MPS, 1),
                    ],
                    "tracks": [conflict.first, conflict.second],
                    "movements": [
                        passage.movement if passage else None for passage in (first, second)
                    ],
                    "passages": [
                        str(passage.id) if passage else None for passage in (first, second)
                    ],
                    "ground_m": [round(conflict.x, 1), round(conflict.y, 1)],
                    "pixel": [round(float(pixel_x)), round(float(pixel_y))],
                },
                "clip_id": None,
            }
        )
        return [event]
