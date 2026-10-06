"""Stop-line crossings against the signal: what the passage records and what raises an event."""

import copy
from collections.abc import Sequence
from typing import Any

from test_geometry import FPS, at, nothing
from test_passages import CAR, THROUGH, Step, box, gone
from test_signals import AMBER, GREEN, RED, RED_AMBER, SIGNAL_SITE, UNKNOWN

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event, Passage, SignalSource, SignalState
from trafficcam.detectors.red_light import RedLight
from trafficcam.geometry import SceneGeometry
from trafficcam.passages import PassageBuilder
from trafficcam.signals import Reading, Signals

SITE: dict[str, Any] = copy.deepcopy(SIGNAL_SITE)
SITE["detectors"] = {"red_light": {"grace_s": 0.5}}
CONFIG = SiteConfig.model_validate(SITE)
# THROUGH crosses the stop line on frame 9.
CROSSED = 9


def drive(
    *changes: tuple[str, SignalState, int],
    config: SiteConfig = CONFIG,
    path: Sequence[Step] = THROUGH,
) -> tuple[Passage, list[Event]]:
    """One vehicle through the junction, with each head changing state at the given frames."""
    signals = Signals(config, "sha256:test")
    scene = SceneGeometry(config)
    builder = PassageBuilder(config, "sha256:test", signals)
    detector = RedLight(config, "sha256:test", signals)
    passages, events = [], []
    for frame, position in enumerate(gone(path)):
        readings = {
            head: Reading(state, at(since)) for head, state, since in changes if since == frame
        }
        signals.update(at(frame), readings)
        tracks = nothing() if position is None else box(*position, CAR, track_id=7)
        observation = scene.observe(tracks, at(frame))
        detector.update(observation, at(frame))
        closed = builder.update(observation, at(frame))
        passages += closed
        events += [event for passage in closed for event in detector.passage_closed(passage)]
    (passage,) = passages
    return passage, events


def test_crossing_on_a_red_that_has_lasted_raises_a_red_light_event() -> None:
    passage, (event,) = drive(("near", RED, 0), ("far", RED, 0))

    assert (passage.signal_state_at_crossing, passage.signal_source) == (
        RED,
        SignalSource.observed,
    )
    assert event.type == "red_light"
    assert event.ts == passage.stopline_crossed_at == at(CROSSED)
    assert event.attrs == {
        "line": "stopline",
        "time_into_red_s": round(CROSSED / FPS, 2),
        "movement": "south->north",
    }
    assert event.passage_id == passage.id


def test_crossing_just_after_the_change_to_red_is_let_off() -> None:
    # The second head went red 0.47 s before the crossing: inside the half second of grace.
    passage, events = drive(("near", RED, 0), ("far", RED, 2))

    assert passage.signal_state_at_crossing == RED
    assert events == []


def test_crossing_on_amber_raises_an_amber_event() -> None:
    passage, (event,) = drive(("near", AMBER, 3), ("far", AMBER, 3))

    assert passage.signal_state_at_crossing == AMBER
    assert event.type == "amber_crossing"
    assert event.attrs["time_into_amber_s"] == round((CROSSED - 3) / FPS, 2)


def test_amber_events_can_be_turned_off() -> None:
    site = copy.deepcopy(SITE)
    site["detectors"]["red_light"]["amber_events"] = False

    _, events = drive(("near", AMBER, 3), ("far", AMBER, 3), config=SiteConfig.model_validate(site))

    assert events == []


def test_crossing_on_red_and_amber_or_green_is_recorded_but_raises_nothing() -> None:
    early, early_events = drive(("near", RED_AMBER, 0), ("far", RED_AMBER, 0))
    legal, legal_events = drive(("near", GREEN, 0), ("far", GREEN, 0))

    assert (early.signal_state_at_crossing, legal.signal_state_at_crossing) == (RED_AMBER, GREEN)
    assert early_events == legal_events == []


def test_heads_that_disagree_or_are_unknown_raise_nothing() -> None:
    split, split_events = drive(("near", RED, 0), ("far", GREEN, 0))
    blind, blind_events = drive(("near", UNKNOWN, 0), ("far", UNKNOWN, 0))

    for passage in (split, blind):
        assert (passage.signal_state_at_crossing, passage.signal_source) == (UNKNOWN, None)
    assert split_events == blind_events == []


def test_one_readable_head_is_enough() -> None:
    passage, (event,) = drive(("near", RED, 0), ("far", UNKNOWN, 0))

    assert passage.signal_state_at_crossing == RED
    assert event.type == "red_light"


def test_a_line_with_no_heads_records_no_signal() -> None:
    site = copy.deepcopy(SITE)
    for head in site["signal_heads"].values():
        head["controls"] = []

    passage, events = drive(("near", RED, 0), config=SiteConfig.model_validate(site))

    assert (passage.signal_state_at_crossing, passage.signal_source) == (None, None)
    assert events == []


def test_edging_over_the_line_on_red_and_waiting_for_green_is_not_running_it() -> None:
    # Over the line on frame 9, then standing just past it until the lights change.
    waiting = [*THROUGH[: CROSSED + 1], *[THROUGH[CROSSED]] * 60, *THROUGH[CROSSED + 1 :]]
    green_at = CROSSED + 40

    passage, events = drive(
        ("near", RED, 0),
        ("far", RED, 0),
        ("near", RED_AMBER, green_at - 30),
        ("far", RED_AMBER, green_at - 30),
        ("near", GREEN, green_at),
        ("far", GREEN, green_at),
        path=waiting,
    )

    assert passage.signal_state_at_crossing == RED
    assert events == []


def test_a_vehicle_lost_right_after_the_line_raises_nothing() -> None:
    passage, events = drive(("near", RED, 0), ("far", RED, 0), path=THROUGH[: CROSSED + 2])

    assert passage.stopline == "stopline"
    assert events == []


def test_where_the_rear_is_tracked_the_signal_is_read_as_the_front_crossed() -> None:
    site = copy.deepcopy(SITE)
    site["lines"]["stopline"]["lag_s"] = 0.4  # six frames
    lagging = SiteConfig.model_validate(site)
    # Red from frame 5: the rear crosses on frame 9, but the front crossed on frame 3.
    changes = [
        (head, state, frame) for head in ("near", "far") for state, frame in ((AMBER, 0), (RED, 5))
    ]

    passage, (event,) = drive(*changes, config=lagging)
    without_lag, _ = drive(*changes)

    assert (passage.signal_state_at_crossing, without_lag.signal_state_at_crossing) == (AMBER, RED)
    assert event.type == "amber_crossing"
    assert event.attrs["time_into_amber_s"] == round(3 / FPS, 2)
    assert passage.stopline_crossed_at == at(CROSSED)
