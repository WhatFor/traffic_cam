"""Near misses and incident candidates, on made-up tracks through a junction, 10 px to a metre."""

import copy
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pytest
import supervision as sv
from test_geometry import FPS, SITE, at
from test_passages import CAR

from trafficcam.config import SiteConfig
from trafficcam.contracts import Event
from trafficcam.detectors import Detector
from trafficcam.detectors.conflicts import Conflicts
from trafficcam.detectors.incident import Incidents
from trafficcam.detectors.near_miss import NearMiss
from trafficcam.geometry import SceneGeometry
from trafficcam.passages import PassageBuilder

# The junction of test_geometry, with a second road across it from west to east.
CROSSROADS: dict[str, Any] = copy.deepcopy(SITE)
CROSSROADS["zones"] |= {
    "approach_west": {
        "role": "approach",
        "arm": "west",
        "polygon": [[400, 500], [790, 500], [790, 700], [400, 700]],
    },
    "exit_east": {
        "role": "exit",
        "arm": "east",
        "polygon": [[1210, 500], [1600, 500], [1600, 700], [1210, 700]],
    },
}
CROSSROADS["detectors"] = {
    "near_miss": {"pet_max_s": 1.5},
    "incident": {
        "contact_s": 0.5,
        "standstill_after_s": 20,
        "lone_standstill_s": 60,
        "min_confidence": 0.5,
        "notify_min_confidence": 0.7,
    },
}
CONFIG = SiteConfig.model_validate(CROSSROADS)

Place = tuple[float, float]
Path = Callable[[int], Place | None]  # where a track is in a frame, in pixels
STEP = 10.0  # pixels a frame: a metre a frame, 15 m/s, 34 mph


def northbound(meets_at: int, x: float = 1000.0, stops: bool = False) -> Path:
    """Up the image from the south arm, at the junction's middle (y 600) in frame `meets_at`."""

    def place(frame: int) -> Place | None:
        y = 600.0 + STEP * (meets_at - frame)
        if stops and frame > meets_at:
            return (x, 590.0)
        return (x, y) if 150 <= y <= 1250 else None

    return place


def eastbound(meets_at: int, y: float = 600.0, stops: bool = False) -> Path:
    """Left to right from the west arm, at the junction's middle (x 1000) in frame `meets_at`."""

    def place(frame: int) -> Place | None:
        x = 1000.0 - STEP * (meets_at - frame)
        if stops and frame > meets_at:
            return (1010.0, y)
        return (x, y) if 420 <= x <= 1580 else None

    return place


def standing(where: Place, since: int, until: int) -> Path:
    return lambda frame: where if since <= frame < until else None


def play(tracks: dict[int, Path], frames: int, only: type | None = None) -> list[Event]:
    """Run the tracks through both detectors; returns the events, of one detector if asked."""
    scene = SceneGeometry(CONFIG)
    builder = PassageBuilder(CONFIG, "sha256:test")
    near_miss = CONFIG.detectors.near_miss
    assert near_miss is not None
    conflicts = Conflicts(near_miss, widest_gap_s=1.5)
    detectors: Sequence[Detector] = [
        NearMiss(CONFIG, "sha256:test", conflicts),
        Incidents(CONFIG, "sha256:test", conflicts),
    ]
    events = []
    for frame in range(frames):
        places = {track: path(frame) for track, path in tracks.items()}
        seen = {track: place for track, place in places.items() if place is not None}
        boxes = np.array([[x - 20, y - 30, x + 20, y] for x, y in seen.values()], dtype=np.float32)
        observation = scene.observe(
            sv.Detections(
                xyxy=boxes.reshape(-1, 4),
                confidence=np.full(len(seen), 0.9, dtype=np.float32),
                class_id=np.full(len(seen), CAR),
                tracker_id=np.array(list(seen), dtype=np.int_),
            ),
            at(frame),
        )
        for detector in detectors:
            if only is None or isinstance(detector, only):
                events += detector.update(observation, at(frame))
        for passage in builder.update(observation, at(frame)):
            for detector in detectors:
                if only is None or isinstance(detector, only):
                    events += detector.passage_closed(passage)
    return events


def test_crossing_paths_a_moment_apart_are_a_near_miss() -> None:
    (event,) = play({1: northbound(meets_at=70), 2: eastbound(meets_at=82)}, 250, only=NearMiss)

    assert event.type == "near_miss"
    assert event.ts == at(82)
    assert event.track_id == 2
    assert event.attrs["pet_s"] == pytest.approx(12 / FPS, abs=0.01)
    assert event.attrs["tracks"] == [1, 2]
    assert event.attrs["angle_deg"] == 90
    assert event.attrs["speeds_mph"] == [pytest.approx(33.6, abs=0.2)] * 2
    assert event.attrs["movements"] == ["south->north", "west->east"]
    assert event.attrs["pixel"] == [1000, 600]
    assert all(event.attrs["passages"])


def test_paths_that_cross_well_apart_are_not() -> None:
    assert play({1: northbound(meets_at=70), 2: eastbound(meets_at=100)}, 250, only=NearMiss) == []


def test_one_vehicle_following_another_is_not() -> None:
    assert play({1: northbound(meets_at=70), 2: northbound(meets_at=80)}, 250, only=NearMiss) == []


def test_two_from_the_same_arm_are_not_whatever_their_headings() -> None:
    def turning_right(frame: int) -> Place | None:
        """Up from the south arm, then right across the junction and out to the east."""
        if frame <= 74:
            return northbound(meets_at=70)(frame)
        x = 1000.0 + STEP * (frame - 74)
        return (x, 560.0) if x <= 1580 else None

    def veering(frame: int) -> Place | None:
        """Just behind it from the same arm, cutting diagonally across where it turned."""
        start, (x0, y0), (x1, y1) = 60, (900.0, 1000.0), (1100.0, 400.0)
        share = (frame - start) / 45
        return (x0 + (x1 - x0) * share, y0 + (y1 - y0) * share) if 0 <= share <= 1.6 else None

    assert play({1: turning_right, 2: veering}, 300, only=NearMiss) == []


def test_a_track_too_new_to_have_a_heading_is_not_trusted() -> None:
    def appears_late(frame: int) -> Place | None:
        return eastbound(meets_at=82)(frame) if frame >= 76 else None

    assert play({1: northbound(meets_at=70), 2: appears_late}, 250, only=NearMiss) == []


def test_a_waiting_vehicles_box_stretched_by_one_passing_in_front_is_not_a_meeting() -> None:
    def waiting_to_turn(frame: int) -> Place | None:
        """Creeping forward in the junction; for a few frames its box takes in the passing car."""
        if 78 <= frame <= 82:
            return eastbound(meets_at=80, y=640.0)(frame)
        return (1000.0, 620.0 - 3.0 * (frame - 40)) if 40 <= frame <= 130 else None

    events = play({1: waiting_to_turn, 2: eastbound(meets_at=80, y=640.0)}, 300)

    assert events == []


def test_two_that_meet_and_one_stops_dead_but_drives_on_are_only_a_near_miss() -> None:
    def brakes_then_goes(frame: int) -> Place | None:
        """Stops for three seconds just past the meeting point, then carries on east."""
        if frame <= 73:
            return eastbound(meets_at=73)(frame)
        if frame <= 73 + 3 * FPS:
            return (1000.0, 600.0)
        return eastbound(meets_at=73 + 3 * FPS)(frame)

    events = play({1: northbound(meets_at=70), 2: brakes_then_goes}, 70 + 40 * FPS)

    assert [event.type for event in events] == ["near_miss"]


def test_two_that_meet_and_stay_are_an_incident_candidate() -> None:
    crash = {1: northbound(meets_at=70, stops=True), 2: eastbound(meets_at=73, stops=True)}

    (event,) = play(crash, 70 + 30 * FPS, only=Incidents)

    assert event.type == "incident_candidate"
    assert event.ts == at(73)
    assert event.confidence == pytest.approx(0.9)
    assert event.attrs["signs"] == ["contact", "sudden_stop", "standstill"]
    assert event.attrs["standing"] == [1, 2]
    assert event.attrs["pet_s"] == pytest.approx(3 / FPS, abs=0.01)


def test_two_that_meet_and_drive_on_are_only_a_near_miss() -> None:
    events = play({1: northbound(meets_at=70), 2: eastbound(meets_at=73)}, 70 + 40 * FPS)

    assert [event.type for event in events] == ["near_miss"]


def test_a_long_standstill_in_the_junction_is_a_candidate_by_itself() -> None:
    (event,) = play({1: standing((1000.0, 600.0), 10, 10 + 70 * FPS)}, 80 * FPS, only=Incidents)

    assert event.ts == at(10)
    assert event.confidence == pytest.approx(0.5)
    assert event.attrs["signs"] == ["lone_standstill"]
    assert event.attrs["standing_s"] == 60
    assert event.passage_id is None


def test_waiting_to_turn_or_at_a_stop_line_is_not_an_incident() -> None:
    waiting_to_turn = {1: standing((1000.0, 600.0), 10, 10 + 30 * FPS)}
    at_the_stop_line = {1: standing((1000.0, 1000.0), 10, 10 + 90 * FPS)}

    assert play(waiting_to_turn, 50 * FPS, only=Incidents) == []
    assert play(at_the_stop_line, 100 * FPS, only=Incidents) == []


def test_pulling_over_beyond_the_junction_and_staying_is_a_candidate() -> None:
    # What the two cars did after the collision of 2026-10-07.
    pulled_over = {1: standing((1400.0, 600.0), 10, 10 + 90 * FPS)}

    (event,) = play(pulled_over, 100 * FPS, only=Incidents)

    assert event.attrs["signs"] == ["lone_standstill"]


def test_a_queue_through_the_junction_is_not_an_incident() -> None:
    queue = {
        track: standing((1000.0, 500.0 + 80 * track), 10, 10 + 70 * FPS) for track in range(1, 4)
    }

    assert play(queue, 80 * FPS, only=Incidents) == []
