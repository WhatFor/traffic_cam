"""The pipeline takes scripted detections through tracking, geometry and passages."""

import json
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

import numpy as np
import supervision as sv
from test_geometry import FPS, at
from test_passages import CAR, CONFIG, LOST_FRAMES, THROUGH

from trafficcam.contracts import Event, Passage
from trafficcam.detectors import Detector
from trafficcam.geometry import Observation, SceneGeometry
from trafficcam.inference import InferenceBackend
from trafficcam.passages import PassageBuilder
from trafficcam.pipeline import FrameResult, Pipeline
from trafficcam.sources import Frame
from trafficcam.tracking.bytetrack import ByteTracker

NO_IMAGE = np.empty((0, 0, 3), dtype=np.uint8)
EXAMPLES = Path(__file__).parents[2] / "contracts" / "examples"
CONFIG_HASH = "sha256:test"


def detection(x: float, y: float) -> sv.Detections:
    """A 100x60 car whose ground point is at (x, y), with boxes that are not round numbers."""
    return sv.Detections(
        xyxy=np.array([[x - 50.13, y - 60.27, x + 49.87, y]], dtype=np.float32),
        confidence=np.array([0.83], dtype=np.float32),
        class_id=np.array([CAR]),
    )


# One car through the junction, then enough empty frames for its passage to close.
DRIVE = [detection(*position) for position in THROUGH]
DRIVE_AND_GONE = [*DRIVE, *[sv.Detections.empty()] * LOST_FRAMES]


class ScriptedBackend:
    def __init__(self, script: Sequence[sv.Detections]) -> None:
        self._script = script

    def detect(self, frame: Frame) -> sv.Detections:
        return self._script[frame.index]


def frames(count: int) -> Iterator[Frame]:
    for index in range(count):
        yield Frame(index=index, timestamp=at(index), image=NO_IMAGE)


def pipeline(backend: InferenceBackend, detectors: Sequence[Detector] = ()) -> Pipeline:
    return Pipeline(
        backend,
        ByteTracker(CONFIG.tracking, FPS),
        SceneGeometry(CONFIG),
        PassageBuilder(CONFIG, CONFIG_HASH),
        detectors,
    )


def an_event(event_type: str = "box_junction_stop") -> Event:
    """The contract's example event."""
    example = json.loads((EXAMPLES / "event.json").read_text())
    return Event.model_validate(example | {"type": event_type})


def run(script: Sequence[sv.Detections]) -> tuple[Pipeline, list[FrameResult]]:
    running = pipeline(ScriptedBackend(script))
    return running, [running.process(frame) for frame in frames(len(script))]


def test_a_drive_through_the_junction_closes_one_passage() -> None:
    _, results = run(DRIVE_AND_GONE)

    assert results[0].detections is DRIVE_AND_GONE[0]
    assert results[0].inference_ms >= 0
    assert any(len(result.observation.tracks) for result in results)
    (passage,) = [passage for result in results for passage in result.passages]
    assert passage.movement == "south->north"
    assert passage.stopline == "stopline"


def test_flush_closes_a_passage_that_is_still_open() -> None:
    running, results = run(DRIVE)

    assert not any(result.passages for result in results)
    (passage,), events = running.flush()
    assert passage.last_seen == at(len(DRIVE) - 1)
    assert events == []


def test_flush_before_any_frame_returns_nothing() -> None:
    assert pipeline(ScriptedBackend([])).flush() == ([], [])


class EveryPassage:
    """A detector that raises the example event for every passage, and one per frame 3."""

    def __init__(self) -> None:
        self.frames = 0

    def update(self, observation: Observation, timestamp: datetime) -> list[Event]:
        self.frames += 1
        return [an_event("on_frame")] if self.frames == 3 else []

    def passage_closed(self, passage: Passage) -> list[Event]:
        return [an_event("on_passage")]


def test_events_from_detectors_are_on_the_frame_that_raised_them() -> None:
    running = pipeline(ScriptedBackend(DRIVE_AND_GONE), [EveryPassage()])
    results = [running.process(frame) for frame in frames(len(DRIVE_AND_GONE))]

    assert [event.type for event in results[2].events] == ["on_frame"]
    (closing,) = [result for result in results if result.passages]
    assert [event.type for event in closing.events] == ["on_passage"]
    assert sum(len(result.events) for result in results) == 2


def test_flush_gives_the_events_of_the_passages_it_closes() -> None:
    running = pipeline(ScriptedBackend(DRIVE), [EveryPassage()])
    for frame in frames(len(DRIVE)):
        running.process(frame)

    passages, events = running.flush()

    assert len(passages) == 1
    assert [event.type for event in events] == ["on_passage"]
