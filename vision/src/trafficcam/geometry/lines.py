"""Line crossings: which tracks have just crossed a configured line the way that counts."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import numpy.typing as npt

from trafficcam.config import Line

Points = npt.NDArray[np.float64]  # n x 2


@dataclass(frozen=True, slots=True)
class Crossing:
    track_id: int
    line: str
    timestamp: datetime  # of the first frame on the far side


def counted_normal(line: Line, junction: Points) -> Points:
    """Unit vector across the line, pointing the way a crossing counts.

    `inbound` is towards `junction`, a point; `outbound` is away from it.
    """
    start, end = (np.array(point, dtype=np.float64) for point in line.points)
    along = end - start
    normal = np.array([-along[1], along[0]]) / np.linalg.norm(along)
    towards_junction = np.dot(junction - start, normal) > 0
    return normal if towards_junction == (line.direction == "inbound") else -normal


@dataclass(slots=True)
class _TrackState:
    crossed_side: bool  # confirmed: is the track beyond the line, the way that counts
    last_seen: datetime
    pending: int = 0
    pending_since: datetime | None = None
    reported: bool = False


class LineCrossings:
    """Follows every track's side of one line and reports each track's crossing once."""

    def __init__(self, name: str, line: Line, junction: Points, lost_s: float) -> None:
        self._name = name
        self._confirm_frames = line.confirm_frames
        self._lost = timedelta(seconds=lost_s)
        self._start, end = (np.array(point, dtype=np.float64) for point in line.points)
        self._length = float(np.linalg.norm(end - self._start))
        self._along = (end - self._start) / self._length
        self._normal = counted_normal(line, junction)
        self._states: dict[int, _TrackState] = {}

    def update(
        self, track_ids: npt.NDArray[np.integer], points: Points, timestamp: datetime
    ) -> list[Crossing]:
        relative = points - self._start
        distance_along = relative @ self._along
        # Beyond either end of the line a track's side says nothing about crossing it.
        level = (distance_along >= 0) & (distance_along <= self._length)
        beyond = relative @ self._normal > 0

        crossings = []
        for track_id, is_level, is_beyond in zip(track_ids.tolist(), level, beyond, strict=True):
            state = self._states.get(track_id)
            if state is None:
                if is_level:
                    self._states[track_id] = _TrackState(bool(is_beyond), timestamp)
                continue
            state.last_seen = timestamp
            if not is_level:
                continue
            if is_beyond == state.crossed_side:
                state.pending = 0
                continue
            if state.pending == 0:
                state.pending_since = timestamp
            state.pending += 1
            if state.pending < self._confirm_frames:
                continue
            state.crossed_side = bool(is_beyond)
            state.pending = 0
            if state.crossed_side and not state.reported and state.pending_since is not None:
                state.reported = True
                crossings.append(Crossing(track_id, self._name, state.pending_since))

        self._states = {
            track_id: state
            for track_id, state in self._states.items()
            if timestamp - state.last_seen <= self._lost
        }
        return crossings
