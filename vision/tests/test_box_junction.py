"""Box junction stops: who stood in the box, for how long, and whether they were allowed to."""

import copy
import uuid
from collections.abc import Sequence
from typing import Any

from test_geometry import FPS, SITE, at, nothing
from test_passages import CAR, box

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage, RoadUserClass
from trafficcam.detectors.box_junction import BoxJunctionStops
from trafficcam.geometry import SceneGeometry
from trafficcam.passages import PassageBuilder

# The site from the geometry tests, with a second exit to the right of the box. Traffic
# arrives from the south, so leaving to the east is the right turn.
RIGHT_TURN_SITE: dict[str, Any] = copy.deepcopy(SITE)
RIGHT_TURN_SITE["zones"]["exit_east"] = {
    "role": "exit",
    "arm": "east",
    "polygon": [[1250, 500], [1500, 500], [1500, 700], [1250, 700]],
}
RIGHT_TURN_SITE["movements"] = {"right_turn": {"from": "approach", "to": "exit_east"}}
RIGHT_TURN_SITE["detectors"] = {
    "box_junction": {
        "min_stationary_s": 3.0,
        "exempt_movements": ["right_turn"],
        "stationary_radius_px": 10,
    }
}
CONFIG = SiteConfig.model_validate(RIGHT_TURN_SITE)

Point = tuple[float, float]
GONE: list[Point | None] = [None] * (int(CONFIG.tracking.lost_s * FPS) + 2)
# 60 frames in one place is 59 frame intervals: 3.93 s.
LONG, SHORT = 60, 40


def up(x: float, start: int, stop: int) -> list[Point]:
    """Driving up the image at 40 px a frame."""
    return [(x, float(y)) for y in range(start, stop, -40)]


def stand(point: Point, frames: int) -> list[Point]:
    return [point] * frames


IN_BOX = (1000.0, 600.0)
ARRIVE = up(1000, 1240, 600)  # through the approach and into the box, 40 px short of IN_BOX
LEAVE_NORTH = up(1000, 560, 130)  # out through the exit ahead
LEAVE_EAST = [(float(x), 600.0) for x in range(1040, 1440, 40)]  # out through the exit on the right


def drive(path: Sequence[Point | None]) -> tuple[list[Passage], list[Event]]:
    """Feed one track along `path` and on until its passage has closed."""
    scene = SceneGeometry(CONFIG)
    builder = PassageBuilder(CONFIG, "sha256:test")
    detector = BoxJunctionStops(CONFIG, "sha256:test")
    passages, events = [], []
    for frame, point in enumerate([*path, *GONE]):
        tracks = nothing() if point is None else box(*point, CAR, track_id=7)
        observation = scene.observe(tracks, at(frame))
        events += detector.update(observation, at(frame))
        closed = builder.update(observation, at(frame))
        passages += closed
        events += [event for passage in closed for event in detector.passage_closed(passage)]
    return passages, events


def test_a_through_vehicle_that_stands_in_the_box_raises_an_event() -> None:
    (passage,), (event,) = drive([*ARRIVE, *stand(IN_BOX, LONG), *LEAVE_NORTH])

    assert event.type == "box_junction_stop"
    assert event.ts == at(len(ARRIVE))
    assert event.attrs == {
        "stationary_s": round((LONG - 1) / FPS, 2),
        "zone": "box",
        "movement": "south->north",
        "x": 1000,
        "y": 600,
    }
    assert (event.passage_id, event.track_id, event.class_) == (passage.id, 7, RoadUserClass.car)
    assert (event.camera, event.config_hash) == (CONFIG.camera.id, "sha256:test")
    assert event.confidence is None


def test_a_vehicle_waiting_to_turn_right_raises_nothing() -> None:
    (passage,), events = drive([*ARRIVE, *stand(IN_BOX, LONG), *LEAVE_EAST])

    assert passage.movement == "south->east"
    assert events == []


def test_a_stop_shorter_than_the_minimum_raises_nothing() -> None:
    _, events = drive([*ARRIVE, *stand(IN_BOX, SHORT), *LEAVE_NORTH])

    assert events == []


def test_a_standing_vehicle_whose_box_wobbles_still_counts() -> None:
    wobble = [(IN_BOX[0] + dx, IN_BOX[1] + dy) for dx, dy in [(0, 0), (4, -2), (-5, 3), (6, 2)]]

    _, events = drive([*ARRIVE, *wobble * (LONG // 4), *LEAVE_NORTH])

    assert len(events) == 1


def test_a_vehicle_creeping_through_the_box_raises_nothing() -> None:
    creep = [(1000.0, 780.0 - 3 * frame) for frame in range(120)]

    _, events = drive([*up(1000, 1250, 780), *creep, *up(1000, 400, 130)])

    assert events == []


def test_standing_outside_the_box_raises_nothing() -> None:
    queue = stand((1000.0, 1010.0), LONG)

    _, events = drive([*up(1000, 1250, 1010), *queue, *up(1000, 970, 130)])

    assert events == []


def test_an_unknown_movement_raises_nothing() -> None:
    no_exit, no_exit_events = drive([*ARRIVE, *stand(IN_BOX, LONG)])
    no_entry, no_entry_events = drive([*stand(IN_BOX, LONG), *LEAVE_NORTH])

    assert (no_exit[0].entry_zone, no_exit[0].exit_zone) == ("approach", None)
    assert (no_entry[0].entry_zone, no_entry[0].exit_zone) == (None, "exit")
    assert no_exit_events == no_entry_events == []


def test_the_longest_of_several_stops_is_reported() -> None:
    first, second = stand((1000.0, 720.0), LONG), stand((1000.0, 480.0), LONG + 30)
    path = [*up(1000, 1250, 720), *first, *up(1000, 680, 480), *second, *up(1000, 440, 130)]

    _, (event,) = drive(path)

    assert event.attrs["stationary_s"] == round((LONG + 29) / FPS, 2)
    assert event.ts == at(path.index((1000.0, 480.0)))


def test_the_event_id_follows_from_the_passage() -> None:
    path = [*ARRIVE, *stand(IN_BOX, LONG), *LEAVE_NORTH]

    (passage,), (first,) = drive(path)
    _, (second,) = drive(path)

    assert first.id == second.id == uuid.uuid5(passage.id, "box_junction_stop")
