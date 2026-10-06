"""Banned turns: an event for each passage that makes a forbidden movement."""

import copy
import uuid
from collections.abc import Sequence
from typing import Any

from test_box_junction import ARRIVE, GONE, LEAVE_EAST, LEAVE_NORTH, RIGHT_TURN_SITE, Point
from test_geometry import FPS, at, nothing
from test_passages import CAR, box

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage, SignalState
from trafficcam.detectors.banned_turn import BannedTurns
from trafficcam.geometry import SceneGeometry
from trafficcam.passages import PassageBuilder
from trafficcam.signals import Reading, Signals

# The box junction test site, where the turn to the east exit is now forbidden.
SITE: dict[str, Any] = copy.deepcopy(RIGHT_TURN_SITE)
SITE["detectors"] = {"banned_turns": {"movements": ["right_turn"]}}
CONFIG = SiteConfig.model_validate(SITE)


def drive(path: Sequence[Point | None]) -> tuple[list[Passage], list[Event]]:
    scene = SceneGeometry(CONFIG)
    builder = PassageBuilder(CONFIG, "sha256:test")
    detector = BannedTurns(CONFIG, "sha256:test")
    passages, events = [], []
    for frame, point in enumerate([*path, *GONE]):
        tracks = nothing() if point is None else box(*point, CAR, track_id=7)
        observation = scene.observe(tracks, at(frame))
        events += detector.update(observation, at(frame))
        closed = builder.update(observation, at(frame))
        passages += closed
        events += [event for passage in closed for event in detector.passage_closed(passage)]
    return passages, events


def test_a_passage_that_makes_the_banned_turn_raises_an_event() -> None:
    (passage,), (event,) = drive([*ARRIVE, *LEAVE_EAST])

    assert event.type == "banned_turn"
    assert event.attrs == {"turn": "right_turn", "movement": "south->east"}
    assert event.ts == passage.first_seen == at(0)
    assert (event.passage_id, event.track_id) == (passage.id, 7)
    assert event.id == uuid.uuid5(passage.id, "banned_turn")


def test_other_movements_raise_nothing() -> None:
    (passage,), events = drive([*ARRIVE, *LEAVE_NORTH])

    assert passage.movement == "south->north"
    assert events == []


def test_a_passage_without_an_exit_raises_nothing() -> None:
    _, events = drive(ARRIVE)

    assert events == []


def test_a_track_seen_in_another_arms_exit_is_not_believed() -> None:
    # Up through the north exit and then, as if the id had passed to another vehicle,
    # back down and out to the east.
    back_down = [(1000.0, float(y)) for y in range(170, 620, 40)]

    (passage,), events = drive([*ARRIVE, *LEAVE_NORTH, *back_down, *LEAVE_EAST])

    assert passage.movement == "south->east"
    assert events == []


def test_the_event_notes_what_the_listed_signals_were_showing() -> None:
    site = copy.deepcopy(SITE)
    site["signal_heads"] = {"side": {"lamps": {"green": [90, 30, 3, 3]}}}
    site["detectors"]["banned_turns"]["signal_heads"] = ["side"]
    config = SiteConfig.model_validate(site)
    signals = Signals(config, "sha256:test")
    signals.update(at(0), {"side": Reading(SignalState.red, at(0))})
    signals.update(at(6), {"side": Reading(SignalState.green, at(6))})
    scene = SceneGeometry(config)
    builder = PassageBuilder(config, "sha256:test", signals)
    detector = BannedTurns(config, "sha256:test", signals)

    events = []
    path = [*ARRIVE, *LEAVE_EAST]
    for frame, point in enumerate([*path, *GONE]):
        tracks = nothing() if point is None else box(*point, CAR, track_id=7)
        observation = scene.observe(tracks, at(frame))
        detector.update(observation, at(frame))
        for passage in builder.update(observation, at(frame)):
            events += detector.passage_closed(passage)

    (event,) = events
    assert event.attrs["signals"] == {
        "side": {"at_start": "red", "green_s": round((len(path) - 1 - 6) / FPS, 1)}
    }
