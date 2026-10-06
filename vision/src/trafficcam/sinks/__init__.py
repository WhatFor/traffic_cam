"""Sinks: where the pipeline's records go."""

from typing import Protocol

from trafficcam.contracts import Event, Passage


class EventSink(Protocol):
    def passage(self, passage: Passage) -> None: ...

    def event(self, event: Event) -> None: ...

    def close(self) -> None: ...
