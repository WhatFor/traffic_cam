"""Finding when a signal changed from the steps in its lamps' scores.

Reading a lamp against levels learned over minutes fails when the light on the head keeps
changing: the levels move by as much as a lit lamp adds (ADR 0015). But a lamp switches
within a frame or two and the light drifts over seconds, so the second after a change still
differs from the second before it. Given roughly when a change is due, from the plan of the
signals, this looks for the moment at which every lamp of the group steps the way that
change makes it step.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import NamedTuple

import numpy as np
import numpy.typing as npt

from trafficcam.config import SiteConfig
from trafficcam.signals.estimator import AMBER_S, OFF, RED_AMBER_S, SEQUENCE_LAMPS
from trafficcam.signals.lamps import lamp_score
from trafficcam.sources import Rgb

# A step is the middle of the second after a frame less the middle of the second before.
SIDE_S = 1.0
# How much of the lamps' recent scores is kept to look through.
KEEP_S = 90.0
# No lamp's step counts for more than this many times its usual size, so that one bright
# lamp cannot stand in for the rest; and one that steps the wrong way cannot undo them.
MOST = 6.0
LEAST = -2.0
# A lamp's usual step is never taken to be smaller than this: the camera's own noise.
QUIET = 0.5
# For each head with all three lamps, the steps must add up to this, and to this many times
# what anything else in the window does. Four steps of a head add up to 24 at the most; in
# sunshine a real change has scored 13 to 20 a head and nothing else over 8.
ENOUGH_PER_HEAD = 9.0
CLEAR_OF_RIVALS = 2.0
# What is within this of the best moment is the same change, not a rival to it.
SAME_S = 2.5


class Found(NamedTuple):
    at: datetime
    strength: float


class StepFinder:
    """Keeps each lamp's recent scores, and finds changes in them."""

    def __init__(self, config: SiteConfig) -> None:
        self._fps = config.camera.fps
        self._full_heads = {
            name
            for name, head in config.signal_heads.items()
            if set(head.lamps) == set(SEQUENCE_LAMPS)
        }
        self._times: deque[datetime] = deque(maxlen=int(KEEP_S * self._fps))
        self._scores: dict[str, deque[float]] = {
            f"{head}/{lamp}": deque(maxlen=int(KEEP_S * self._fps))
            for head in self._full_heads
            for lamp in SEQUENCE_LAMPS
        }

    @property
    def reach(self) -> timedelta:
        """How far back it can look."""
        return timedelta(seconds=KEEP_S - 2 * SIDE_S - RED_AMBER_S)

    @property
    def settle(self) -> timedelta:
        """How long after a change it takes to have seen all of it."""
        return timedelta(seconds=AMBER_S + SIDE_S + 0.5)

    def add(self, timestamp: datetime, samples: Mapping[str, Rgb]) -> None:
        if not all(name in samples for name in self._scores):
            return
        self._times.append(timestamp)
        for name, scores in self._scores.items():
            scores.append(lamp_score(samples[name], name.rpartition("/")[2]))

    def find(
        self, heads: Sequence[str], edge: str, earliest: datetime, latest: datetime
    ) -> Found | None:
        """When, between two moments, the heads' green started (`on`) or ended (`off`).

        None unless one moment stands clear of every other.
        """
        heads = [head for head in heads if head in self._full_heads]
        side = int(SIDE_S * self._fps)
        amber, red_amber = int(AMBER_S * self._fps), int(RED_AMBER_S * self._fps)
        times = list(self._times)
        if not heads or len(times) < 4 * side + amber:
            return None
        total = np.zeros(len(times))
        for head in heads:
            red, amber_lamp, green = (
                _steps(np.array(self._scores[f"{head}/{lamp}"]), side) for lamp in SEQUENCE_LAMPS
            )
            if edge == OFF:
                # Green goes out and amber comes on; 3 s later amber goes out and red comes on.
                parts = [-green, amber_lamp, -_ahead(amber_lamp, amber), _ahead(red, amber)]
            else:
                # Amber came on beside red 2 s before; now both go out and green comes on.
                parts = [_ahead(amber_lamp, -red_amber), -red, -amber_lamp, green]
            total += np.sum([np.clip(part, LEAST, MOST) for part in parts], axis=0)

        # Only where the whole of the change's steps is in what has been kept.
        first = max(_index(times, earliest), side + red_amber)
        last = min(_index(times, latest), len(times) - side - amber)
        if last <= first:
            return None
        best = first + int(np.argmax(total[first:last]))
        strength = float(total[best])
        same = int(SAME_S * self._fps)
        rivals = np.concatenate([total[first : max(first, best - same)], total[best + same : last]])
        rival = float(rivals.max()) if len(rivals) else 0.0
        if strength < ENOUGH_PER_HEAD * len(heads) or strength < CLEAR_OF_RIVALS * rival:
            return None
        # The steps of a strong change are at their most for several frames: take their middle.
        near = np.arange(max(first, best - side), min(last, best + side + 1))
        top = near[total[near] >= 0.9 * strength]
        return Found(times[int(round(float(top.mean())))], strength)


def _steps(scores: npt.NDArray[np.float64], side: int) -> npt.NDArray[np.float64]:
    """At each frame, the step in a lamp's score, in units of how much it usually steps."""
    steps = np.zeros(len(scores))
    middles = np.median(np.lib.stride_tricks.sliding_window_view(scores, side), axis=1)
    # middles[i] is of the frames from i: the second from a frame, less the second up to it.
    steps[side : len(scores) - side + 1] = middles[side:] - middles[: len(middles) - side]
    usual = 1.48 * float(np.median(np.abs(steps[side:-side])))
    return steps / max(usual, QUIET)


def _ahead(steps: npt.NDArray[np.float64], frames: int) -> npt.NDArray[np.float64]:
    """The steps this many frames after each frame (before it, if negative)."""
    shifted = np.zeros(len(steps))
    if frames >= 0:
        shifted[: len(steps) - frames] = steps[frames:]
    else:
        shifted[-frames:] = steps[:frames]
    return shifted


def _index(times: Sequence[datetime], moment: datetime) -> int:
    """The first frame at or after a moment."""
    low, high = 0, len(times)
    while low < high:
        middle = (low + high) // 2
        if times[middle] < moment:
            low = middle + 1
        else:
            high = middle
    return low
