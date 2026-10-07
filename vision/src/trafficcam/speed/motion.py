"""Each track's motion over the road: where it is, how fast it is going and which way."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from trafficcam.geometry import Observation

# A track seen again after longer than this has a hole in it; nothing is measured across it.
MAX_GAP = timedelta(seconds=0.25)
# No vehicle covers the ground this fast. A step like it is the tracker moving an id to
# another vehicle, or a box jumping to take in a neighbour.
MAX_STEP_MPS = 60.0
# A window may be this much short, for frames that arrive a little early.
WINDOW_SLACK = timedelta(seconds=0.04)
FORGET_AFTER = timedelta(seconds=5)

_Sample = tuple[datetime, float, float]  # when, metres east, metres north


@dataclass(frozen=True, slots=True)
class Motion:
    at: datetime  # the middle of the time the velocity was measured over
    x: float  # metres east and north, now
    y: float
    vx: float  # metres a second
    vy: float
    followed_s: float  # how long the track has been followed without a break
    travelled_m: float  # how far it now is from where that began
    # How far the positions lie from the straight line fitted to them, root mean square,
    # in metres. Large when the box jumped: taking in a vehicle passing in front, or
    # switching between two detections of one vehicle.
    wobble_m: float

    @property
    def speed(self) -> float:
        return float(np.hypot(self.vx, self.vy))


@dataclass(slots=True)
class _Run:
    """An unbroken stretch of one track inside the mapped area."""

    began: _Sample
    recent: list[_Sample]  # no more of it than the window needs


class GroundMotion:
    """Fits a straight line to each track's last `window_s` of positions.

    Every position in the window counts, so the wobble of a detection box mostly averages
    out. A track has no motion until it has been followed for a whole window without a
    break, a jump or leaving the mapped area.
    """

    def __init__(self, window_s: float) -> None:
        self._window = timedelta(seconds=window_s) - WINDOW_SLACK
        self._runs: dict[int, _Run] = {}

    def update(self, observation: Observation, timestamp: datetime) -> dict[int, Motion]:
        """Take in one frame. Returns the motion of each track that has one."""
        ids = observation.tracks.tracker_id
        ground, mapped = observation.ground_m, observation.mapped
        if ids is None or ground is None or mapped is None:
            return {}
        motions = {}
        for index, track_id in enumerate(ids.tolist()):
            if not mapped[index]:
                self._runs.pop(track_id, None)
                continue
            sample = (timestamp, float(ground[index, 0]), float(ground[index, 1]))
            motion = self._follow(track_id, sample)
            if motion is not None:
                motions[track_id] = motion
        for track_id in [
            track_id
            for track_id, run in self._runs.items()
            if timestamp - run.recent[-1][0] > FORGET_AFTER
        ]:
            del self._runs[track_id]
        return motions

    def _follow(self, track_id: int, sample: _Sample) -> Motion | None:
        run = self._runs.get(track_id)
        if run is not None:
            when, x, y = run.recent[-1]
            seconds = (sample[0] - when).total_seconds()
            too_far = np.hypot(sample[1] - x, sample[2] - y) > MAX_STEP_MPS * seconds
            if sample[0] - when > MAX_GAP or too_far:
                run = None
        if run is None:
            run = self._runs[track_id] = _Run(sample, [])
        recent = run.recent
        recent.append(sample)
        # Keep the shortest run that still spans the window.
        while len(recent) > 2 and sample[0] - recent[1][0] >= self._window:
            del recent[0]
        if sample[0] - recent[0][0] < self._window:
            return None
        vx, vy, wobble = _fitted_velocity(recent)
        return Motion(
            at=recent[0][0] + (sample[0] - recent[0][0]) / 2,
            x=sample[1],
            y=sample[2],
            vx=vx,
            vy=vy,
            wobble_m=wobble,
            followed_s=(sample[0] - run.began[0]).total_seconds(),
            travelled_m=float(np.hypot(sample[1] - run.began[1], sample[2] - run.began[2])),
        )


def _fitted_velocity(samples: list[_Sample]) -> tuple[float, float, float]:
    """The line that best fits the positions over time: metres a second east and north, and
    how far the positions lie from it."""
    start = samples[0][0]
    seconds = np.array([(when - start).total_seconds() for when, _, _ in samples])
    positions = np.array([(x, y) for _, x, y in samples])
    seconds -= seconds.mean()
    positions -= positions.mean(axis=0)
    velocity = seconds @ positions / (seconds @ seconds)
    off_line = positions - np.outer(seconds, velocity)
    wobble = float(np.sqrt((off_line**2).sum(axis=1).mean()))
    return float(velocity[0]), float(velocity[1]), wobble
