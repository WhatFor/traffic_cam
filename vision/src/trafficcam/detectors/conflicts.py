"""Conflicts: two tracks on crossing paths that reach the same spot a short time apart.

The time between the first leaving a spot and the second arriving is the post-encroachment
time, a standard measure of how close two road users came. A track is one point here, the
bottom centre of its box, so the gap between two vehicles' bodies is smaller than the gap
measured: by the time a vehicle takes to pass a point, about half a second at 20 mph.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from trafficcam.config import NearMiss
from trafficcam.geometry import Observation
from trafficcam.speed import MPH_PER_MPS
from trafficcam.speed.motion import GroundMotion, Motion

# Headings are fitted over this long: short enough to follow a turn.
MOTION_WINDOW_S = 0.5
# A track must also have been going much the same way over this long. A vehicle waiting to
# turn has its box stretched by each vehicle that passes in front of it, which throws its
# ground point onto the other's path and back within a second: over the short window that
# looks like motion, over this one it comes to nothing.
STEADY_WINDOW_S = 1.5
STEADY_WITHIN_DEG = 60.0
# Two positions this close are the same spot. About a lane's half width.
SPOT_M = 1.5
# Where a track has been is remembered for this much longer than the widest gap of
# interest. The gap is taken where two paths come closest; if the memory stopped at the
# widest gap, a pair that really met later than that would be caught at the edge of it,
# a little off the true spot, and counted.
REMEMBER_BEYOND_S = 1.0
# A track's heading is not trusted until it has been followed this long and moved this
# far. A track that has just begun is often half of a vehicle that already has one.
MIN_FOLLOWED_S = 1.0
MIN_TRAVELLED_M = 3.0
# Nor while its positions lie further than this from a straight line, as when a box
# switches between two detections of one vehicle. Half of all moving tracks wobble less
# than 0.2 m; one in eight, more.
MAX_WOBBLE_M = 0.5
# Two tracks that began this close together in time and place are usually one vehicle
# that the tracker has given two ids.
BORN_TOGETHER = timedelta(seconds=1)
BORN_TOGETHER_PX = 150.0
FORGET_BIRTH_AFTER = timedelta(seconds=30)

_Cell = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Conflict:
    first: int  # track ids, in the order they passed the spot
    second: int
    gap_s: float
    distance_m: float  # how nearly the two paths were found to meet; the gap is taken there
    at: datetime  # when the second reached the spot
    angle_deg: float  # between the two headings
    first_mps: float
    second_mps: float
    x: float  # the spot: metres east and north
    y: float


@dataclass(frozen=True, slots=True)
class _Visit:
    track_id: int
    at: datetime
    motion: Motion


class Conflicts:
    """Remembers where each moving track has just been, and finds the tracks that cross there.

    Shared by the detectors that start from a conflict. `found` may be called by each of
    them for the same frame; the frame is only taken in once.
    """

    def __init__(self, settings: NearMiss, widest_gap_s: float) -> None:
        self._horizon = timedelta(seconds=widest_gap_s + REMEMBER_BEYOND_S)
        self._min_mps = settings.min_speed_mph / MPH_PER_MPS
        self._min_angle = settings.min_angle_deg
        self._motion = GroundMotion(MOTION_WINDOW_S)
        self._steady = GroundMotion(STEADY_WINDOW_S)
        # When and where each track was first seen, and when last.
        self._born: dict[int, tuple[datetime, float, float, datetime]] = {}
        self._visits: dict[_Cell, list[_Visit]] = defaultdict(list)
        self._at: datetime | None = None
        self._found: list[Conflict] = []
        self._pruned_at: datetime | None = None
        # Every track's motion in the latest frame, for whoever wants to know what became of one.
        self.motions: dict[int, Motion] = {}

    def found(self, observation: Observation, timestamp: datetime) -> list[Conflict]:
        """The conflicts this frame shows: each pair once, where their paths come closest."""
        if timestamp == self._at:
            return self._found
        self._at = timestamp
        self.motions = self._motion.update(observation, timestamp)
        steady = self._steady.update(observation, timestamp)
        self._note_births(observation, timestamp)
        closest: dict[tuple[int, int], Conflict] = {}
        for track_id, motion in self.motions.items():
            if not self._trusted(motion, steady.get(track_id)):
                continue
            column, row = math.floor(motion.x / SPOT_M), math.floor(motion.y / SPOT_M)
            for cell in ((column + dx, row + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                for visit in self._visits.get(cell, ()):
                    conflict = self._conflict(visit, track_id, motion, timestamp)
                    if conflict is None:
                        continue
                    pair = (visit.track_id, track_id)
                    if pair not in closest or conflict.distance_m < closest[pair].distance_m:
                        closest[pair] = conflict
            self._visits[(column, row)].append(_Visit(track_id, timestamp, motion))
        self._prune(timestamp)
        self._found = list(closest.values())
        return self._found

    def _conflict(
        self, visit: _Visit, second: int, motion: Motion, timestamp: datetime
    ) -> Conflict | None:
        gap = timestamp - visit.at
        was = visit.motion
        if visit.track_id == second or gap > self._horizon:
            return None
        if self._born_together(visit.track_id, second):
            return None
        distance = math.hypot(motion.x - was.x, motion.y - was.y)
        if distance > SPOT_M:
            return None
        cosine = (motion.vx * was.vx + motion.vy * was.vy) / (motion.speed * was.speed)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        if angle < self._min_angle:
            return None
        return Conflict(
            first=visit.track_id,
            second=second,
            gap_s=gap.total_seconds(),
            distance_m=distance,
            at=timestamp,
            angle_deg=angle,
            first_mps=was.speed,
            second_mps=motion.speed,
            x=motion.x,
            y=motion.y,
        )

    def _trusted(self, motion: Motion, steady: Motion | None) -> bool:
        """Whether a track's motion in this frame is a vehicle's and not its box's."""
        if steady is None or min(motion.speed, steady.speed) < self._min_mps:
            return False
        if motion.followed_s < MIN_FOLLOWED_S or motion.travelled_m < MIN_TRAVELLED_M:
            return False
        if motion.wobble_m > MAX_WOBBLE_M:
            return False
        cosine = (motion.vx * steady.vx + motion.vy * steady.vy) / (motion.speed * steady.speed)
        return cosine >= math.cos(math.radians(STEADY_WITHIN_DEG))

    def _note_births(self, observation: Observation, timestamp: datetime) -> None:
        ids = observation.tracks.tracker_id
        if ids is None:
            return
        for track_id, (x, y) in zip(ids.tolist(), observation.ground_points, strict=True):
            when, born_x, born_y, _ = self._born.get(
                track_id, (timestamp, float(x), float(y), None)
            )
            self._born[track_id] = (when, born_x, born_y, timestamp)

    def _born_together(self, one: int, other: int) -> bool:
        a, b = self._born.get(one), self._born.get(other)
        if a is None or b is None:
            return False
        return (
            abs(a[0] - b[0]) <= BORN_TOGETHER
            and math.hypot(a[1] - b[1], a[2] - b[2]) <= BORN_TOGETHER_PX
        )

    def _prune(self, timestamp: datetime) -> None:
        if self._pruned_at is not None and timestamp - self._pruned_at < self._horizon:
            return
        self._pruned_at = timestamp
        for cell in list(self._visits):
            recent = [
                visit for visit in self._visits[cell] if timestamp - visit.at <= self._horizon
            ]
            if recent:
                self._visits[cell] = recent
            else:
                del self._visits[cell]
        for track_id in [
            track_id
            for track_id, (_, _, _, last_seen) in self._born.items()
            if timestamp - last_seen > FORGET_BIRTH_AFTER
        ]:
            del self._born[track_id]
