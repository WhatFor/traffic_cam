"""The per-frame steps: detect, track, place in the scene, build passages."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import supervision as sv

from trafficcam.contracts import Passage
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
    ) -> None:
        self._backend = backend
        self._tracker = tracker
        self._scene = scene
        self._passages = passages
        self._last_timestamp: datetime | None = None

    def process(self, frame: Frame) -> FrameResult:
        started = time.perf_counter()
        detections = self._backend.detect(frame)
        inference_ms = (time.perf_counter() - started) * 1000
        tracks = self._tracker.update(detections, frame.timestamp)
        observation = self._scene.observe(tracks, frame.timestamp)
        self._last_timestamp = frame.timestamp
        return FrameResult(
            frame=frame,
            detections=detections,
            inference_ms=inference_ms,
            observation=observation,
            passages=self._passages.update(observation, frame.timestamp),
        )

    def flush(self) -> list[Passage]:
        """Close every open passage, as at the end of a replay."""
        if self._last_timestamp is None:
            return []
        return self._passages.flush(self._last_timestamp)


def run(
    source: FrameSource,
    pipeline: Pipeline,
    observers: Sequence[FrameObserver],
    sinks: Sequence[EventSink],
) -> None:
    """Process every frame of `source`, handing results to `observers` and passages to `sinks`."""

    def publish(passages: list[Passage]) -> None:
        for passage in passages:
            for sink in sinks:
                sink.passage(passage)

    for frame in source.frames():
        result = pipeline.process(frame)
        for observer in observers:
            observer.observe(result)
        publish(result.passages)
    # Only a replay gets here.
    publish(pipeline.flush())
