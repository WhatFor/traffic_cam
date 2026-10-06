"""Passages: one record per road user's trip through the junction."""

import math
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from trafficcam.config import SiteConfig
from trafficcam.contracts import Passage
from trafficcam.geometry import Crossing, Observation
from trafficcam.inference import CLASS_NAMES
from trafficcam.signals import Signals
from trafficcam.speed import SpeedMeter, SpeedResult

# A heading is taken from where a track was first seen in a zone to where it is once it
# has been followed for this long and has moved this far; less than that is mostly jitter.
HEADING_AFTER = timedelta(seconds=0.5)
HEADING_MIN_PX = 20.0


@dataclass(slots=True)
class _OpenPassage:
    first_seen: datetime
    last_seen: datetime
    class_counts: Counter[int] = field(default_factory=Counter)
    entry_zone: str | None = None
    exit_zone: str | None = None
    crossing: Crossing | None = None
    seen_in_a_zone: bool = False
    # Approach zones that depend on heading: when and where the track was first seen in
    # each, or None once the zone has been ruled out.
    first_in: dict[str, tuple[datetime, float, float] | None] = field(default_factory=dict)


def _speed_flags(speed: SpeedResult) -> dict[str, object]:
    """What is known of a passage's speed beyond the one number the contract has a field for."""
    flags: dict[str, object] = {}
    if speed.sustained_at is not None:
        flags["sustained_at"] = speed.sustained_at.isoformat()
    if speed.stretch_kmh:
        flags["stretch_kmh"] = speed.stretch_kmh
    return flags


class PassageBuilder:
    """Follows every track and returns its passage once the track has gone for good."""

    def __init__(
        self,
        config: SiteConfig,
        config_hash: str,
        signals: Signals | None = None,
        speeds: SpeedMeter | None = None,
    ) -> None:
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._signals = signals
        self._speeds = speeds
        self._zones = config.zones
        self._line_lag = {
            name: timedelta(seconds=line.lag_s) for name, line in config.lines.items()
        }
        # After this long unseen, the tracker will not bring a track back.
        self._lost = timedelta(seconds=config.tracking.lost_s)
        self._open: dict[int, _OpenPassage] = {}

    def update(self, observation: Observation, timestamp: datetime) -> list[Passage]:
        tracks = observation.tracks
        if self._speeds is not None:
            self._speeds.update(observation, timestamp)
        ids = tracks.tracker_id if tracks.tracker_id is not None else []
        class_ids = tracks.class_id if tracks.class_id is not None else []
        for index, (track_id, class_id) in enumerate(zip(ids, class_ids, strict=True)):
            state = self._open.setdefault(int(track_id), _OpenPassage(timestamp, timestamp))
            state.last_seen = timestamp
            state.class_counts[int(class_id)] += 1
            x, y = (float(value) for value in observation.ground_points[index])
            for name in observation.zones_of(index):
                state.seen_in_a_zone = True
                zone = self._zones[name]
                if zone.role == "exit":
                    state.exit_zone = name
                elif zone.role == "approach" and state.entry_zone is None:
                    if zone.entry_heading is None:
                        state.entry_zone = name
                    else:
                        state.first_in.setdefault(name, (timestamp, x, y))
            if state.entry_zone is None:
                self._enter_by_heading(state, timestamp, x, y)
        for crossing in observation.crossings:
            state = self._open[crossing.track_id]
            if state.crossing is None:
                state.crossing = crossing
        return self._close(timestamp, lambda state: timestamp - state.last_seen > self._lost)

    def _enter_by_heading(
        self, state: _OpenPassage, timestamp: datetime, x: float, y: float
    ) -> None:
        """Settle the zones waiting on a heading, once the track has moved enough to have one."""
        for name, first in state.first_in.items():
            if first is None:
                continue
            since, first_x, first_y = first
            if timestamp - since < HEADING_AFTER:
                continue
            if math.hypot(x - first_x, y - first_y) < HEADING_MIN_PX:
                continue
            # Image y grows downwards, so it is negated for a heading that turns anticlockwise.
            heading = math.degrees(math.atan2(first_y - y, x - first_x)) % 360
            if self._zones[name].accepts_heading(heading):
                state.entry_zone = name
                return
            state.first_in[name] = None

    def flush(self, timestamp: datetime) -> list[Passage]:
        """Close every open passage, as at the end of a replay."""
        return self._close(timestamp, lambda _: True)

    def _close(
        self, timestamp: datetime, finished: Callable[[_OpenPassage], bool]
    ) -> list[Passage]:
        closing = {track_id: state for track_id, state in self._open.items() if finished(state)}
        passages = []
        for track_id, state in closing.items():
            del self._open[track_id]
            # Taken for every track, so the meter forgets the ones that make no passage.
            speed = self._speeds.take(track_id) if self._speeds is not None else None
            # A track that never touched a zone is not a trip through the junction.
            if state.seen_in_a_zone:
                passages.append(self._passage(track_id, state, timestamp, speed))
        return passages

    def _passage(
        self, track_id: int, state: _OpenPassage, closed_at: datetime, speed: SpeedResult | None
    ) -> Passage:
        (class_id, _), *_ = state.class_counts.most_common(1)
        entry, exit_ = state.entry_zone, state.exit_zone
        # Nothing leaves by the arm it came in on. Where an arm's approach and exit zones
        # overlap, arriving traffic is seen in the exit zone too.
        if entry is not None and exit_ is not None:
            if self._zones[entry].arm == self._zones[exit_].arm:
                exit_ = None
        movement = None
        if entry is not None and exit_ is not None:
            movement = f"{self._zones[entry].arm}->{self._zones[exit_].arm}"
        # Looked up now, not when the line was crossed: by now the signal's state at that
        # moment has settled.
        signal = None
        if state.crossing is not None and self._signals is not None:
            line = state.crossing.line
            signal = self._signals.line_state(line, state.crossing.timestamp - self._line_lag[line])
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
                "signal_state_at_crossing": signal.state if signal else None,
                "signal_source": signal.source if signal else None,
                "speed_kmh": speed.sustained_kmh if speed else None,
                "flags": {"speed": _speed_flags(speed)} if speed and _speed_flags(speed) else {},
            }
        )
