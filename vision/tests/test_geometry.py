"""Ground points, zone membership and line crossings, on synthetic tracks."""

import copy
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import supervision as sv
from test_config import FULL

from trafficcam.config import SiteConfig
from trafficcam.geometry import Crossing, SceneGeometry

FPS = 15
START = datetime(2026, 10, 4, 13, 5, tzinfo=UTC)

# A square junction with a horizontal stop line below it: inbound traffic drives up the image.
SITE: dict[str, Any] = copy.deepcopy(FULL) | {
    "junction": "box",
    "zones": {
        "box": {"polygon": [[800, 400], [1200, 400], [1200, 800], [800, 800]]},
        "approach": {
            "role": "approach",
            "arm": "south",
            "polygon": [[900, 900], [1100, 900], [1100, 1300], [900, 1300]],
        },
        "exit": {
            "role": "exit",
            "arm": "north",
            "polygon": [[900, 100], [1100, 100], [1100, 380], [900, 380]],
        },
    },
    "lines": {"stopline": {"points": [[900, 900], [1100, 900]], "direction": "inbound"}},
    "movements": {},
    "signal_heads": {},
    "detectors": {},
}


def scene(**line: Any) -> SceneGeometry:
    site = copy.deepcopy(SITE)
    site["lines"]["stopline"] |= line
    return SceneGeometry(SiteConfig.model_validate(site))


def at(frame: int) -> datetime:
    return START + timedelta(seconds=frame / FPS)


def track(x: float, y: float, track_id: int = 7) -> sv.Detections:
    """A 100x60 box whose bottom-centre, its ground point, is at (x, y)."""
    return sv.Detections(
        xyxy=np.array([[x - 50, y - 60, x + 50, y]], dtype=np.float32),
        confidence=np.array([0.9], dtype=np.float32),
        class_id=np.array([2]),
        tracker_id=np.array([track_id]),
    )


def nothing() -> sv.Detections:
    empty = sv.Detections.empty()
    empty.tracker_id = np.array([], dtype=int)
    return empty


def drive(geometry: SceneGeometry, path: Sequence[tuple[float, float] | None]) -> list[Crossing]:
    """Observe one track at each position in turn; `None` is a frame where it is missing."""
    crossings = []
    for frame, position in enumerate(path):
        tracks = nothing() if position is None else track(*position)
        crossings += geometry.observe(tracks, at(frame)).crossings
    return crossings


def test_ground_point_is_the_bottom_centre_of_the_box() -> None:
    observation = scene().observe(track(1000, 1000), START)

    assert observation.ground_points.tolist() == [[1000, 1000]]


def test_zone_membership_follows_the_ground_point() -> None:
    geometry = scene()

    # The box of the first track reaches into the junction zone; its ground point does not.
    below_the_box = geometry.observe(track(1000, 850), START)
    in_the_approach = geometry.observe(track(1000, 1000), START)

    assert below_the_box.zones_of(0) == []
    assert in_the_approach.zones_of(0) == ["approach"]


def test_crossing_towards_the_junction_is_reported_once_with_its_first_far_side_frame() -> None:
    crossings = drive(scene(), [(1000, 950), (1000, 920), (1000, 890), (1000, 860), (1000, 830)])

    assert crossings == [Crossing(track_id=7, line="stopline", timestamp=at(2))]


def test_crossing_away_from_the_junction_is_not_reported_for_an_inbound_line() -> None:
    assert drive(scene(), [(1000, 850), (1000, 880), (1000, 910), (1000, 940)]) == []


def test_an_outbound_line_reports_the_opposite_direction() -> None:
    crossings = drive(
        scene(direction="outbound"), [(1000, 850), (1000, 880), (1000, 910), (1000, 940)]
    )

    assert [crossing.timestamp for crossing in crossings] == [at(2)]


def test_jitter_shorter_than_confirm_frames_is_not_a_crossing() -> None:
    waiting = [(1000, 905), (1000, 898), (1000, 904), (1000, 897), (1000, 906)]

    assert drive(scene(confirm_frames=2), waiting) == []


def test_passing_beyond_the_end_of_the_line_is_not_a_crossing() -> None:
    assert drive(scene(), [(1200, 950), (1200, 900), (1200, 850), (1200, 800)]) == []


def test_a_crossing_made_while_the_track_is_missing_is_still_reported() -> None:
    crossings = drive(
        scene(), [(1000, 950), (1000, 920), None, None, None, None, (1000, 780), (1000, 750)]
    )

    assert [crossing.timestamp for crossing in crossings] == [at(6)]


def test_crossing_drifting_back_and_crossing_again_is_reported_once() -> None:
    path = [(1000, 930), (1000, 880), (1000, 870), (1000, 930), (1000, 940), (1000, 880)]

    assert len(drive(scene(), [*path, (1000, 860)])) == 1


def test_a_track_unseen_for_longer_than_lost_s_starts_afresh() -> None:
    lost_frames = int(SITE["tracking"]["lost_s"] * FPS) + 2
    path = [(1000, 950), (1000, 940), *[None] * lost_frames, (1000, 850), (1000, 840)]

    # Its side before the gap is forgotten, so reappearing beyond the line is not a crossing.
    assert drive(scene(), path) == []
