"""Signals: the state of each signal head, now and in the recent past."""

import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple, Protocol

from trafficcam.config import SiteConfig
from trafficcam.contracts import SignalChange, SignalSource, SignalState
from trafficcam.signals.estimator import NullEstimator, PhaseEstimator
from trafficcam.sources import Frame, Rgb

if TYPE_CHECKING:
    from trafficcam.signals.steps import StepFinder

KEEP = timedelta(minutes=15)
# Each head is checked against the plan of the signals this often, for a moment this far
# back, by when its reading of that moment has settled.
CHECK_EVERY = timedelta(seconds=1)
CHECK_DELAY = timedelta(seconds=3)
# A head is doubted once more than this share of its checks over this long have disagreed
# with the plan. In the dark a head disagrees in under 1 check in 1,000; a sunlit one that
# misreads does in 1 in 10 or more.
DOUBT_OVER = timedelta(minutes=10)
DOUBT_ABOVE = 0.02
MIN_CHECKS = 60


class Reading(NamedTuple):
    """What a head shows in one frame, after flicker and glitches have been voted out."""

    state: SignalState
    since: datetime  # the first frame it showed this
    confidence: float | None = None


class SignalReader(Protocol):
    def read(self, frame: Frame) -> Mapping[str, Reading]: ...


@dataclass(frozen=True, slots=True)
class LineState:
    """The signal a stop line is under, agreed between the heads that control it."""

    state: SignalState
    source: SignalSource | None  # None when unknown
    since: datetime | None  # when the last of its heads changed to this state
    # For an inferred state: how closely the change that began it is placed, in seconds.
    placed_within_s: float | None = None


UNKNOWN = LineState(SignalState.unknown, None, None)


class _Credit:
    """Whether each head's readings have lately agreed with what the plan says of it."""

    def __init__(self) -> None:
        self._checks: dict[str, deque[tuple[datetime, bool]]] = {}
        self.doubted: set[str] = set()

    def note(self, head: str, at: datetime, agreed: bool) -> bool:
        """Record one check. True if it is the one that brings the head into doubt."""
        checks = self._checks.setdefault(head, deque())
        checks.append((at, agreed))
        while at - checks[0][0] > DOUBT_OVER:
            checks.popleft()
        disagreed = sum(1 for _, each in checks if not each)
        doubted = len(checks) >= MIN_CHECKS and disagreed > DOUBT_ABOVE * len(checks)
        newly = doubted and head not in self.doubted
        (self.doubted.add if doubted else self.doubted.discard)(head)
        return newly


class Signals:
    """Keeps each head's recent states, and reports the changes as contract records.

    Where no head can say what a line is under, the estimator is asked. A head that keeps
    contradicting the plan of the signals is doubted: where the plan rules out what it
    shows, it is left out, and what it is seen to do is not passed to the estimator.
    """

    def __init__(
        self,
        config: SiteConfig,
        config_hash: str,
        estimator: PhaseEstimator | None = None,
        steps: "StepFinder | None" = None,
    ) -> None:
        self._config = config
        self._estimator = estimator or NullEstimator()
        # Looks in the lamps' own scores for the changes the estimator places only loosely.
        self._steps = steps
        self._credit = _Credit()
        self._checked_at: datetime | None = None
        self._camera = config.camera.id
        self._config_hash = config_hash
        # Per head, oldest first: (since, state).
        self._history: dict[str, list[tuple[datetime, SignalState]]] = {
            name: [] for name in config.signal_heads
        }

    def update(
        self,
        timestamp: datetime,
        readings: Mapping[str, Reading],
        samples: Mapping[str, Rgb] | None = None,
    ) -> list[SignalChange]:
        """Take a frame's readings, and the lamp colours they came from if there are any."""
        if self._steps is not None and samples:
            self._steps.add(timestamp, samples)
        changes = []
        for head, reading in readings.items():
            if head not in self._history:
                continue
            history = self._history[head]
            previous = history[-1][1] if history else None
            if reading.state == previous:
                continue
            # A reading can only be dated after what is already recorded.
            since = max(reading.since, history[-1][0]) if history else reading.since
            history.append((since, reading.state))
            if head not in self._credit.doubted:
                self._estimator.observe(head, previous, reading.state, since)
            while len(history) > 2 and timestamp - history[1][0] > KEEP:
                del history[0]
            changes.append(
                SignalChange.model_validate(
                    {
                        "id": uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"trafficcam/{self._camera}/{head}/{since.isoformat()}",
                        ),
                        "ts": since,
                        "camera": self._camera,
                        "config_hash": self._config_hash,
                        "head_id": head,
                        "from_state": previous,
                        "to_state": reading.state,
                        "source": self.source_of(head, reading.state),
                        "confidence": reading.confidence,
                    }
                )
            )
        if self._checked_at is None or timestamp - self._checked_at >= CHECK_EVERY:
            self._checked_at = timestamp
            self._check(timestamp - CHECK_DELAY)
            self._look_for_loose_changes(timestamp)
        return changes

    @property
    def doubted(self) -> frozenset[str]:
        """The heads whose readings are not believed where the plan rules them out."""
        return frozenset(self._credit.doubted)

    def current(self) -> dict[str, SignalState]:
        return {
            head: history[-1][1] if history else SignalState.unknown
            for head, history in self._history.items()
        }

    def source_of(self, head: str, state: SignalState) -> SignalSource:
        # A head whose red the camera cannot see is taken to be red whenever it is not green.
        if state == SignalState.red and "red" not in self._config.signal_heads[head].lamps:
            return SignalSource.inferred
        return SignalSource.observed

    def state_of(self, head: str, at: datetime) -> tuple[SignalState, datetime | None]:
        """The head's state at a moment, and since when it had been in it."""
        for since, state in reversed(self._history[head]):
            if since <= at:
                return state, since
        return SignalState.unknown, None

    def line_state(self, target: str, at: datetime) -> LineState | None:
        """The signal a stop line or movement is under; None if nothing controls it.

        Heads that are unknown are left out. If the rest disagree, the answer is unknown.
        If none is left, it is whatever the estimator can work out, marked as inferred.
        """
        heads = self._config.heads_controlling(target)
        known = [
            (head, state, since)
            for head in heads
            for state, since in [self.state_of(head, at)]
            if state != SignalState.unknown
            and since is not None
            and not self._overruled(head, state, at)
        ]
        states = {state for _, state, _ in known}
        if len(states) > 1:
            return UNKNOWN
        if not states:
            inferred = self._estimator.state_of(target, at)
            if inferred is None:
                return UNKNOWN if heads else None
            if inferred.state == SignalState.unknown:
                return UNKNOWN
            return LineState(
                inferred.state, SignalSource.inferred, inferred.since, inferred.within_s
            )
        (state,) = states
        sources = {self.source_of(head, state) for head, _, _ in known}
        source = (
            SignalSource.inferred if SignalSource.inferred in sources else SignalSource.observed
        )
        return LineState(state, source, max(since for _, _, since in known))

    def _check(self, at: datetime) -> None:
        for head in self._history:
            state, _ = self.state_of(head, at)
            planned = self._estimator.state_for_head(head, at)
            if SignalState.unknown in (state, planned.state if planned else state):
                continue
            if planned and self._credit.note(head, at, planned.state == state):
                # What it was seen to do may have been misread too.
                self._estimator.forget(head)

    def _look_for_loose_changes(self, now: datetime) -> None:
        if self._steps is None:
            return
        # Up to the latest moment whose change would by now have been seen in full.
        seen_to = now - self._steps.settle
        for loose in self._estimator.loosely_placed(seen_to):
            if loose.earliest > seen_to or loose.earliest < now - self._steps.reach:
                continue
            found = self._steps.find(
                loose.heads, loose.edge, loose.earliest, min(loose.latest, seen_to)
            )
            if found is not None:
                self._estimator.found(loose.group, loose.edge, found.at)

    def _overruled(self, head: str, state: SignalState, at: datetime) -> bool:
        if head not in self._credit.doubted:
            return False
        planned = self._estimator.state_for_head(head, at)
        return planned is not None and planned.state not in (SignalState.unknown, state)

    def seconds_in(self, head: str, state: SignalState, start: datetime, end: datetime) -> float:
        """How long the head was in `state` between two moments."""
        history = self._history[head]
        total = timedelta()
        for index, (since, was) in enumerate(history):
            until = history[index + 1][0] if index + 1 < len(history) else end
            if was == state:
                total += max(min(until, end) - max(since, start), timedelta())
        return total.total_seconds()
