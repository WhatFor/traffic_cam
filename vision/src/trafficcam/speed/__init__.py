"""Speed: how fast each track moves over the road, in metres from the ground map.

Two measures. The sustained speed is the fastest a vehicle held for `sustained_s`, fitted
to every position in that time so that the wobble of a detection box averages out. The
stretch average is the distance between where a vehicle entered and left a marked stretch
of road, over the time it took, as an average-speed camera measures it.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import supervision as sv

from trafficcam.config import Speed
from trafficcam.geometry import Observation
from trafficcam.speed.motion import GroundMotion

KMH_PER_MPS = 3.6
KMH_PER_MPH = 1.609344
MPH_PER_MPS = KMH_PER_MPS / KMH_PER_MPH

_Sample = tuple[datetime, float, float]  # when, metres east, metres north


@dataclass(frozen=True, slots=True)
class SpeedResult:
    sustained_kmh: float | None
    sustained_at: datetime | None  # the middle of the second it was held for
    stretch_kmh: dict[str, float]


@dataclass(slots=True)
class _Track:
    peak_mps: float | None = None
    peak_at: datetime | None = None
    # For each stretch the track has been in: the first and latest positions inside it.
    in_stretch: dict[str, tuple[_Sample, _Sample]] = field(default_factory=dict)


class SpeedMeter:
    """Follows every track over the ground and keeps its speeds until they are taken."""

    def __init__(self, settings: Speed) -> None:
        self._motion = GroundMotion(settings.sustained_s)
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
        ground, mapped = observation.ground_m, observation.mapped
        if ids is None or len(ids) == 0 or ground is None or mapped is None:
            return
        motions = self._motion.update(observation, timestamp)
        inside = {
            name: zone.trigger(observation.tracks) for name, (zone, _) in self._stretches.items()
        }
        for index, track_id in enumerate(ids.tolist()):
            track = self._tracks.setdefault(track_id, _Track())
            motion = motions.get(track_id)
            if motion is not None and (track.peak_mps is None or motion.speed > track.peak_mps):
                track.peak_mps, track.peak_at = motion.speed, motion.at
            sample = (timestamp, float(ground[index, 0]), float(ground[index, 1]))
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
