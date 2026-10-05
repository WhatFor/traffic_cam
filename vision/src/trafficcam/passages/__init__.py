"""Passages: one record per road user's trip through the junction."""

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from trafficcam.config import SiteConfig
from trafficcam.contracts import Passage
from trafficcam.geometry import Crossing, Observation
from trafficcam.inference import COCO_CLASS_IDS

CLASS_NAMES = {class_id: name for name, class_id in COCO_CLASS_IDS.items()}


@dataclass(slots=True)
class _OpenPassage:
    first_seen: datetime
    last_seen: datetime
    class_counts: Counter[int] = field(default_factory=Counter)
    entry_zone: str | None = None
    exit_zone: str | None = None
    crossing: Crossing | None = None
    seen_in_a_zone: bool = False


class PassageBuilder:
    """Follows every track and returns its passage once the track has gone for good."""

    def __init__(self, config: SiteConfig, config_hash: str) -> None:
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._zones = config.zones
        # After this long unseen, the tracker will not bring a track back.
        self._lost = timedelta(seconds=config.tracking.lost_s)
        self._open: dict[int, _OpenPassage] = {}

    def update(self, observation: Observation, timestamp: datetime) -> list[Passage]:
        tracks = observation.tracks
        ids = tracks.tracker_id if tracks.tracker_id is not None else []
        class_ids = tracks.class_id if tracks.class_id is not None else []
        for index, (track_id, class_id) in enumerate(zip(ids, class_ids, strict=True)):
            state = self._open.setdefault(int(track_id), _OpenPassage(timestamp, timestamp))
            state.last_seen = timestamp
            state.class_counts[int(class_id)] += 1
            for name in observation.zones_of(index):
                state.seen_in_a_zone = True
                role = self._zones[name].role
                if role == "approach" and state.entry_zone is None:
                    state.entry_zone = name
                elif role == "exit":
                    state.exit_zone = name
        for crossing in observation.crossings:
            state = self._open[crossing.track_id]
            if state.crossing is None:
                state.crossing = crossing
        return self._close(timestamp, lambda state: timestamp - state.last_seen > self._lost)

    def flush(self, timestamp: datetime) -> list[Passage]:
        """Close every open passage, as at the end of a replay."""
        return self._close(timestamp, lambda _: True)

    def _close(
        self, timestamp: datetime, finished: Callable[[_OpenPassage], bool]
    ) -> list[Passage]:
        closing = {track_id: state for track_id, state in self._open.items() if finished(state)}
        for track_id in closing:
            del self._open[track_id]
        # A track that never touched a zone is not a trip through the junction.
        return [
            self._passage(track_id, state, timestamp)
            for track_id, state in closing.items()
            if state.seen_in_a_zone
        ]

    def _passage(self, track_id: int, state: _OpenPassage, closed_at: datetime) -> Passage:
        (class_id, _), *_ = state.class_counts.most_common(1)
        entry, exit_ = state.entry_zone, state.exit_zone
        movement = None
        if entry is not None and exit_ is not None:
            movement = f"{self._zones[entry].arm}->{self._zones[exit_].arm}"
        # Derived from what identifies the trip, so replaying a clip gives the same ids.
        name = f"trafficcam/{self._camera}/{state.first_seen.isoformat()}/{track_id}"
        return Passage.model_validate(
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, name),
                "ts": closed_at,
                "camera": self._camera,
                "config_hash": self._config_hash,
                "first_seen": state.first_seen,
                "last_seen": state.last_seen,
                "track_id": track_id,
                "class": CLASS_NAMES[class_id],
                "entry_zone": entry,
                "exit_zone": exit_,
                "movement": movement,
                "stopline": state.crossing.line if state.crossing else None,
                "stopline_crossed_at": state.crossing.timestamp if state.crossing else None,
                "signal_state_at_crossing": None,
                "signal_source": None,
                "speed_kmh": None,
                "flags": {},
            }
        )
