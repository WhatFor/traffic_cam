"""Speed: how fast each track moves over the road, in metres from the ground map.

Two measures. The sustained speed is the fastest a vehicle held for `sustained_s`, fitted
to every position in that time so that the wobble of a detection box averages out. The
stretch average is the distance between where a vehicle entered and left a marked stretch
of road, over the time it took, as an average-speed camera measures it.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import supervision as sv

from trafficcam.config import Speed
from trafficcam.geometry import Observation

KMH_PER_MPS = 3.6
KMH_PER_MPH = 1.609344
# A track seen again after longer than this has a hole in it; its speed is not carried across.
MAX_GAP = timedelta(seconds=0.25)
# No vehicle covers the ground this fast. A step like it is the tracker moving an id to
# another vehicle, or a box jumping to take in a neighbour.
MAX_STEP_MPS = 60.0
# The window may be this much short of `sustained_s`, for frames that arrive a little early.
WINDOW_SLACK = timedelta(seconds=0.04)

_Sample = tuple[datetime, float, float]  # when, metres east, metres north


@dataclass(frozen=True, slots=True)
class SpeedResult:
    sustained_kmh: float | None
    sustained_at: datetime | None  # the middle of the second it was held for
    stretch_kmh: dict[str, float]


@dataclass(slots=True)
class _Track:
    # An unbroken run of positions inside the mapped area, no longer than the window.
    recent: list[_Sample] = field(default_factory=list)
    peak_mps: float | None = None
    peak_at: datetime | None = None
    # For each stretch the track has been in: the first and latest positions inside it.
    in_stretch: dict[str, tuple[_Sample, _Sample]] = field(default_factory=dict)


class SpeedMeter:
    """Follows every track's ground position and keeps its speeds until they are taken."""

    def __init__(self, settings: Speed) -> None:
        self._map = settings.ground_map()
        self._window = timedelta(seconds=settings.sustained_s)
        self._stretches = {
            name: (
                sv.PolygonZone(
                    np.array(stretch.polygon), triggering_anchors=(sv.Position.BOTTOM_CENTER,)
                ),
                stretch.min_m,
            )
            for name, stretch in settings.stretches.items()
        }
        self._tracks: dict[int, _Track] = {}

    def update(self, observation: Observation, timestamp: datetime) -> None:
        ids = observation.tracks.tracker_id
        if ids is None or len(ids) == 0:
            return
        ground = self._map.to_ground(observation.ground_points)
        mapped = self._map.contains(observation.ground_points)
        inside = {
            name: zone.trigger(observation.tracks) for name, (zone, _) in self._stretches.items()
        }
        for index, track_id in enumerate(ids.tolist()):
            track = self._tracks.setdefault(track_id, _Track())
            sample = (timestamp, float(ground[index, 0]), float(ground[index, 1]))
            if mapped[index]:
                self._sustain(track, sample)
            else:
                track.recent.clear()
            for name, here in inside.items():
                # Outside the mapped area a position is a guess, wherever the stretch is drawn.
                if here[index] and mapped[index]:
                    first, _ = track.in_stretch.get(name, (sample, sample))
                    track.in_stretch[name] = (first, sample)

    def take(self, track_id: int) -> SpeedResult | None:
        """A finished track's speeds. They are forgotten once taken."""
        track = self._tracks.pop(track_id, None)
        if track is None:
            return None
        stretches = {}
        for name, (first, last) in track.in_stretch.items():
            metres = float(np.hypot(last[1] - first[1], last[2] - first[2]))
            seconds = (last[0] - first[0]).total_seconds()
            if metres >= self._stretches[name][1] and seconds > 0:
                stretches[name] = round(metres / seconds * KMH_PER_MPS, 1)
        sustained = None if track.peak_mps is None else round(track.peak_mps * KMH_PER_MPS, 1)
        return SpeedResult(sustained, track.peak_at, stretches)

    def _sustain(self, track: _Track, sample: _Sample) -> None:
        if track.recent:
            when, x, y = track.recent[-1]
            seconds = (sample[0] - when).total_seconds()
            too_far = np.hypot(sample[1] - x, sample[2] - y) > MAX_STEP_MPS * seconds
            if sample[0] - when > MAX_GAP or too_far:
                track.recent.clear()
        track.recent.append(sample)
        # Keep the shortest run that still spans the window.
        while (
            len(track.recent) > 2 and sample[0] - track.recent[1][0] >= self._window - WINDOW_SLACK
        ):
            del track.recent[0]
        if sample[0] - track.recent[0][0] < self._window - WINDOW_SLACK:
            return
        speed = _fitted_speed(track.recent)
        if track.peak_mps is None or speed > track.peak_mps:
            track.peak_mps = speed
            track.peak_at = track.recent[0][0] + (sample[0] - track.recent[0][0]) / 2


def _fitted_speed(samples: list[_Sample]) -> float:
    """Metres a second along the straight line that best fits the positions over time."""
    start = samples[0][0]
    seconds = np.array([(when - start).total_seconds() for when, _, _ in samples])
    positions = np.array([(x, y) for _, x, y in samples])
    seconds -= seconds.mean()
    velocity = seconds @ (positions - positions.mean(axis=0)) / (seconds @ seconds)
    return float(np.hypot(*velocity))
