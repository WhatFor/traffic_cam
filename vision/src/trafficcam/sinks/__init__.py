"""Sinks: where the pipeline's records go."""

from typing import Protocol

from trafficcam.contracts import Passage


class EventSink(Protocol):
    def passage(self, passage: Passage) -> None: ...

    def close(self) -> None: ...
