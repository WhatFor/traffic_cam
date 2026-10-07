"""Red-light and amber crossings: a stop line crossed, and the junction entered, on a bad signal."""

import uuid
from datetime import datetime, timedelta

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage, SignalSource, SignalState
from trafficcam.geometry import Observation
from trafficcam.signals import LineState, Signals

RED_LIGHT = "red_light"
AMBER_CROSSING = "amber_crossing"
DETECTOR_VERSION = "1"


class RedLight:
    """Looks at each passage's stop-line crossing against the signal at that moment.

    A vehicle that edges over the line on red and then waits there has not run the light,
    so an event needs the vehicle to have gone on: to have reached the junction or an exit
    while the signal still had not released it.

    A red counts once it has been red for the grace period, if it was read from the lamps
    or, where `inferred_within_s` is set, worked out from the plan of the signals with its
    start placed that closely. An inferred state is dated from the latest it can have begun,
    so the time into it is the least it can be. Red-and-amber is recorded on the passage but
    raises nothing. A line whose signal is unknown raises nothing.
    """

    def __init__(self, config: SiteConfig, config_hash: str, signals: Signals) -> None:
        settings = config.detectors.red_light
        if settings is None:
            raise ValueError("detectors.red_light is not configured")
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._signals = signals
        self._grace = timedelta(seconds=settings.grace_s)
        self._amber_events = settings.amber_events
        self._inferred_within = settings.inferred_within_s
        self._line_lag = {
            name: timedelta(seconds=line.lag_s) for name, line in config.lines.items()
        }
        self._beyond_zones = [
            name for name, zone in config.zones.items() if zone.role != "approach"
        ]
        # Tracks that have crossed a line, and when each was first seen past its approach.
        self._went_on: dict[int, datetime | None] = {}

    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        for crossing in observation.crossings:
            self._went_on.setdefault(crossing.track_id, None)
        ids = observation.tracks.tracker_id
        if ids is None or not self._went_on:
            return []
        for name in self._beyond_zones:
            for track_id in ids[observation.zones[name]].tolist():
                if self._went_on.get(track_id, timestamp) is None:
                    self._went_on[track_id] = timestamp
        return []

    def passage_closed(self, passage: Passage) -> list[Event]:
        went_on = self._went_on.pop(passage.track_id or -1, None)
        line, crossed_at = passage.stopline, passage.stopline_crossed_at
        if line is None or crossed_at is None or went_on is None:
            return []
        front_crossed_at = crossed_at - self._line_lag[line]
        signal = self._signals.line_state(line, front_crossed_at)
        if signal is None or signal.since is None or not self._counts(signal):
            return []
        later = self._signals.line_state(line, went_on)
        into = front_crossed_at - signal.since
        if signal.state == SignalState.red and into > self._grace:
            if later is not None and later.state == SignalState.red:
                return [
                    self._event(passage, RED_LIGHT, crossed_at, signal, "time_into_red_s", into)
                ]
        if signal.state == SignalState.amber and self._amber_events:
            if later is not None and later.state in (SignalState.amber, SignalState.red):
                return [
                    self._event(
                        passage, AMBER_CROSSING, crossed_at, signal, "time_into_amber_s", into
                    )
                ]
        return []

    def _counts(self, signal: LineState) -> bool:
        if signal.source == SignalSource.observed:
            return True
        within = signal.placed_within_s
        return (
            self._inferred_within is not None
            and within is not None
            and within <= self._inferred_within
        )

    def _event(
        self,
        passage: Passage,
        event_type: str,
        crossed_at: datetime,
        signal: LineState,
        timing: str,
        into: timedelta,
    ) -> Event:
        attrs: dict[str, object] = {
            "line": passage.stopline,
            timing: round(into.total_seconds(), 2),
            "movement": passage.movement,
        }
        if signal.source == SignalSource.inferred:
            attrs["signal_source"] = SignalSource.inferred.value
        return Event.model_validate(
            {
                # Derived from the passage, so a replay gives the same id.
                "id": uuid.uuid5(passage.id, event_type),
                "ts": crossed_at,
                "camera": self._camera,
                "config_hash": self._config_hash,
                "type": event_type,
                "detector_version": DETECTOR_VERSION,
                "passage_id": passage.id,
                "track_id": passage.track_id,
                "class": passage.class_,
                "confidence": None,
                "attrs": attrs,
                "clip_id": None,
            }
        )
