"""The per-frame steps: detect, track, place in the scene, build passages."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import supervision as sv

from trafficcam.contracts import Event, Passage
from trafficcam.detectors import Detector
from trafficcam.geometry import Observation, SceneGeometry
from trafficcam.inference import InferenceBackend
from trafficcam.passages import PassageBuilder
from trafficcam.sinks import EventSink
from trafficcam.sources import Frame, FrameSource
from trafficcam.tracking import Tracker


@dataclass(frozen=True, slots=True)
class FrameResult:
    frame: Frame
    detections: sv.Detections
    inference_ms: float
    observation: Observation
    passages: list[Passage]  # those that closed on this frame
    events: list[Event]


class FrameObserver(Protocol):
    """Something that wants every frame's result, such as a debug view or a log."""

    def observe(self, result: FrameResult) -> None: ...

    def close(self) -> None: ...


class Pipeline:
    def __init__(
        self,
        backend: InferenceBackend,
        tracker: Tracker,
        scene: SceneGeometry,
        passages: PassageBuilder,
        detectors: Sequence[Detector] = (),
    ) -> None:
        self._backend = backend
        self._tracker = tracker
        self._scene = scene
        self._passages = passages
        self._detectors = detectors
        self._last_timestamp: datetime | None = None

    def process(self, frame: Frame) -> FrameResult:
        started = time.perf_counter()
        detections = self._backend.detect(frame)
        inference_ms = (time.perf_counter() - started) * 1000
        tracks = self._tracker.update(detections, frame.timestamp)
        observation = self._scene.observe(tracks, frame.timestamp)
        self._last_timestamp = frame.timestamp
        events = [
            event
            for detector in self._detectors
            for event in detector.update(observation, frame.timestamp)
        ]
        closed = self._passages.update(observation, frame.timestamp)
        return FrameResult(
            frame=frame,
            detections=detections,
            inference_ms=inference_ms,
            observation=observation,
            passages=closed,
            events=events + self._on_closed(closed),
        )

    def flush(self) -> tuple[list[Passage], list[Event]]:
        """Close every open passage, as at the end of a replay."""
        if self._last_timestamp is None:
            return [], []
        closed = self._passages.flush(self._last_timestamp)
        return closed, self._on_closed(closed)

    def _on_closed(self, passages: list[Passage]) -> list[Event]:
        return [
            event
            for detector in self._detectors
            for passage in passages
            for event in detector.passage_closed(passage)
        ]


def run(
    source: FrameSource,
    pipeline: Pipeline,
    observers: Sequence[FrameObserver],
    sinks: Sequence[EventSink],
) -> None:
    """Process every frame of `source`: results go to `observers`, records to `sinks`."""

    def publish(passages: list[Passage], events: list[Event]) -> None:
        for sink in sinks:
            for passage in passages:
                sink.passage(passage)
            for event in events:
                sink.event(event)

    for frame in source.frames():
        result = pipeline.process(frame)
        for observer in observers:
            observer.observe(result)
        publish(result.passages, result.events)
    # Only a replay gets here.
    publish(*pipeline.flush())
