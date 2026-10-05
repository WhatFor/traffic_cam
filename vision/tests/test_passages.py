"""The passage builder turns a track's life into one contract `Passage`."""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta

import numpy as np
import supervision as sv
from test_contracts import validator_for
from test_geometry import FPS, SITE, at, nothing

from trafficcam.config import SiteConfig
from trafficcam.contracts import Passage, RoadUserClass
from trafficcam.geometry import SceneGeometry
from trafficcam.passages import PassageBuilder

CAR, BUS, TRUCK = 2, 5, 7
CONFIG = SiteConfig.model_validate(SITE)
LOST_FRAMES = int(CONFIG.tracking.lost_s * FPS) + 2

# Up the image at x=1000: through the approach, over the stop line at y=900, across the
# junction (y 800 to 400) and into the exit (y 380 to 100).
THROUGH = [(1000.0, float(y)) for y in range(1250, 130, -40)]

Step = tuple[float, float] | None


def box(x: float, y: float, class_id: int, track_id: int) -> sv.Detections:
    return sv.Detections(
        xyxy=np.array([[x - 50, y - 60, x + 50, y]], dtype=np.float32),
        confidence=np.array([0.9], dtype=np.float32),
        class_id=np.array([class_id]),
        tracker_id=np.array([track_id]),
    )


def run(
    path: Sequence[Step], classes: Sequence[int] | None = None, flush: bool = False
) -> tuple[list[Passage], list[int]]:
    """Feed one track along `path`; return the passages and the frames they closed on."""
    scene, builder = SceneGeometry(CONFIG), PassageBuilder(CONFIG, "sha256:test")
    passages, closed_on = [], []
    for frame, position in enumerate(path):
        class_id = CAR if classes is None else classes[frame]
        tracks = nothing() if position is None else box(*position, class_id, track_id=7)
        closed = builder.update(scene.observe(tracks, at(frame)), at(frame))
        passages += closed
        closed_on += [frame] * len(closed)
    if flush:
        passages += builder.flush(at(len(path)))
    return passages, closed_on


def gone(path: Sequence[Step]) -> list[Step]:
    return [*path, *[None] * LOST_FRAMES]


def test_a_trip_through_the_junction_becomes_one_passage() -> None:
    (passage,), _ = run(gone(THROUGH))

    assert passage.track_id == 7
    assert passage.class_ == RoadUserClass.car
    assert (passage.entry_zone, passage.exit_zone, passage.movement) == (
        "approach",
        "exit",
        "south->north",
    )
    assert passage.stopline == "stopline"
    # y falls 40 px a frame from 1250, so the ninth frame is the first beyond y=900.
    assert passage.stopline_crossed_at == at(9)
    assert (passage.first_seen, passage.last_seen) == (at(0), at(len(THROUGH) - 1))
    assert passage.camera == CONFIG.camera.id
    assert passage.config_hash == "sha256:test"


def test_a_passage_closes_only_after_the_track_has_been_lost() -> None:
    passages, closed_on = run(gone(THROUGH))

    last_seen = len(THROUGH) - 1
    lost = timedelta(seconds=CONFIG.tracking.lost_s)
    assert len(passages) == 1
    assert at(closed_on[0] - 1) - at(last_seen) <= lost < at(closed_on[0]) - at(last_seen)
    assert passages[0].ts == at(closed_on[0])


def test_the_class_is_the_most_frequent_one() -> None:
    path = gone(THROUGH)
    classes = [BUS if frame % 3 else TRUCK for frame in range(len(path))]

    (passage,), _ = run(path, classes)

    assert passage.class_ == RoadUserClass.bus


def test_a_track_that_never_enters_a_zone_is_not_a_passage() -> None:
    passages, _ = run(gone([(300.0, float(y)) for y in range(1250, 130, -40)]))

    assert passages == []


def test_a_track_lost_in_the_junction_has_no_exit_or_movement() -> None:
    (passage,), _ = run(gone([step for step in THROUGH if step[1] > 500]))

    assert passage.entry_zone == "approach"
    assert passage.exit_zone is None
    assert passage.movement is None


def test_flush_closes_open_passages() -> None:
    still_open, _ = run(THROUGH)
    flushed, _ = run(THROUGH, flush=True)

    assert still_open == []
    assert [passage.movement for passage in flushed] == ["south->north"]
    assert flushed[0].ts == at(len(THROUGH))


def test_the_same_input_gives_identical_passages() -> None:
    first, _ = run(gone(THROUGH))
    second, _ = run(gone(THROUGH))

    assert first == second
    assert isinstance(first[0].ts, datetime)


def test_a_built_passage_matches_the_contract() -> None:
    (passage,), _ = run(gone(THROUGH))

    validator_for(Passage).validate(json.loads(passage.model_dump_json(by_alias=True)))
