"""Signals: the state of each signal head, now and in the recent past."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple, Protocol

from trafficcam.config import SiteConfig
from trafficcam.contracts import SignalChange, SignalSource, SignalState
from trafficcam.signals.estimator import NullEstimator, PhaseEstimator
from trafficcam.sources import Frame

KEEP = timedelta(minutes=15)


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


UNKNOWN = LineState(SignalState.unknown, None, None)


class Signals:
    """Keeps each head's recent states, and reports the changes as contract records.

    Where no head can say what a line is under, the estimator is asked.
    """

    def __init__(
        self, config: SiteConfig, config_hash: str, estimator: PhaseEstimator | None = None
    ) -> None:
        self._config = config
        self._estimator = estimator or NullEstimator()
        self._camera = config.camera.id
        self._config_hash = config_hash
        # Per head, oldest first: (since, state).
        self._history: dict[str, list[tuple[datetime, SignalState]]] = {
            name: [] for name in config.signal_heads
        }

    def update(self, timestamp: datetime, readings: Mapping[str, Reading]) -> list[SignalChange]:
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
        return changes

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
            if state != SignalState.unknown and since is not None
        ]
        states = {state for _, state, _ in known}
        if len(states) > 1:
            return UNKNOWN
        if not states:
            inferred = self._estimator.state_of(target, at)
            if inferred is None:
                return UNKNOWN if heads else None
            state, since = inferred
            if state == SignalState.unknown:
                return UNKNOWN
            return LineState(state, SignalSource.inferred, since)
        (state,) = states
        sources = {self.source_of(head, state) for head, _, _ in known}
        source = (
            SignalSource.inferred if SignalSource.inferred in sources else SignalSource.observed
        )
        return LineState(state, source, max(since for _, _, since in known))

    def seconds_in(self, head: str, state: SignalState, start: datetime, end: datetime) -> float:
        """How long the head was in `state` between two moments."""
        history = self._history[head]
        total = timedelta()
        for index, (since, was) in enumerate(history):
            until = history[index + 1][0] if index + 1 < len(history) else end
            if was == state:
                total += max(min(until, end) - max(since, start), timedelta())
        return total.total_seconds()
