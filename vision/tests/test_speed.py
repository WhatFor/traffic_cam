"""Speeds come from each track's path over the ground: the fastest held, and stretch averages."""

import copy
from collections.abc import Sequence
from datetime import datetime

import numpy as np
import pytest
import supervision as sv
from test_geometry import FPS, SITE, at
from test_groundmap import MEASURED, pixels_of
from test_passages import CAR, LOST_FRAMES

from trafficcam.config import SiteConfig, Speed
from trafficcam.detectors.speeding import Speeding
from trafficcam.geometry import Observation, SceneGeometry
from trafficcam.groundmap import GroundMap
from trafficcam.passages import PassageBuilder
from trafficcam.speed import KMH_PER_MPH, SpeedMeter

# A stretch of the road in test_groundmap: from 20 m to 40 m east, the full width.
STRETCH = np.array([[20.0, 0.0], [40.0, 0.0], [40.0, 40.0], [20.0, 40.0]])
GROUND = GroundMap(pixels_of(MEASURED).tolist(), MEASURED.tolist())
SETTINGS = Speed.model_validate(
    {
        "stretches": {
            "middle": {"polygon": np.round(pixels_of(STRETCH)).astype(int).tolist(), "min_m": 15}
        },
        "limit_mph": 30,
        "flag_above_mph": 35,
    }
)
Ground = tuple[float, float]


def observe(positions: dict[int, Ground]) -> Observation:
    """Tracks standing at these places on the road, as the camera would see them."""
    pixels = pixels_of(np.array(list(positions.values()), dtype=np.float64).reshape(-1, 2))
    boxes = np.column_stack([pixels[:, 0] - 20, pixels[:, 1] - 30, pixels[:, 0] + 20, pixels[:, 1]])
    tracks = sv.Detections(
        xyxy=boxes.astype(np.float32),
        confidence=np.full(len(positions), 0.9, dtype=np.float32),
        class_id=np.full(len(positions), CAR),
        tracker_id=np.array(list(positions), dtype=np.int_),
    )
    ground_points = tracks.get_anchors_coordinates(sv.Position.BOTTOM_CENTER).astype(np.float64)
    return Observation(
        tracks,
        ground_points,
        zones={},
        crossings=[],
        ground_m=GROUND.to_ground(ground_points),
        mapped=GROUND.contains(ground_points),
    )


def drive(meter: SpeedMeter, path: Sequence[Ground | None], track_id: int = 7) -> None:
    """One frame for each place on the path; None is a frame without the track."""
    for frame, position in enumerate(path):
        meter.update(observe({} if position is None else {track_id: position}), at(frame))


def eastwards(
    mps: float, seconds: float, start_m: float = 5.0, north_m: float = 20.0
) -> list[Ground]:
    return [(start_m + mps * frame / FPS, north_m) for frame in range(round(seconds * FPS) + 1)]


def test_a_steady_drive_reads_its_speed() -> None:
    meter = SpeedMeter(SETTINGS)

    drive(meter, eastwards(mps=13.0, seconds=3))
    speed = meter.take(7)

    assert speed is not None
    assert speed.sustained_kmh == pytest.approx(46.8, abs=0.1)
    assert speed.sustained_at is not None
    assert at(0) < speed.sustained_at < at(3 * FPS)
    assert meter.take(7) is None


def test_wobble_in_the_detections_averages_out() -> None:
    rng = np.random.default_rng(1)
    meter = SpeedMeter(SETTINGS)
    # About 0.2 m either way in each frame: more than a second's worth washes out.
    path = [(x + rng.normal(0, 0.2), y + rng.normal(0, 0.2)) for x, y in eastwards(13.0, 3)]

    drive(meter, path)
    speed = meter.take(7)

    assert speed is not None
    assert speed.sustained_kmh == pytest.approx(46.8, rel=0.06)


def test_the_speed_is_the_fastest_held_for_a_second() -> None:
    meter = SpeedMeter(SETTINGS)
    slow = eastwards(mps=5.0, seconds=2)
    fast = eastwards(mps=15.0, seconds=1.5, start_m=slow[-1][0])
    crawl = eastwards(mps=2.0, seconds=2, start_m=fast[-1][0])

    drive(meter, [*slow, *fast[1:], *crawl[1:]])
    speed = meter.take(7)

    assert speed is not None
    assert speed.sustained_kmh == pytest.approx(54.0, abs=0.1)
    assert speed.sustained_at is not None
    assert at(2 * FPS) < speed.sustained_at < at(int(3.5 * FPS))


def test_a_burst_shorter_than_a_second_does_not_set_the_speed() -> None:
    meter = SpeedMeter(SETTINGS)
    slow = eastwards(mps=5.0, seconds=2)
    burst = eastwards(mps=15.0, seconds=0.4, start_m=slow[-1][0])
    after = eastwards(mps=5.0, seconds=2, start_m=burst[-1][0])

    drive(meter, [*slow, *burst[1:], *after[1:]])
    speed = meter.take(7)

    assert speed is not None and speed.sustained_kmh is not None
    assert 18.0 <= speed.sustained_kmh < 40.0


def test_a_hole_in_the_track_is_not_measured_across() -> None:
    meter = SpeedMeter(SETTINGS)
    before = eastwards(mps=13.0, seconds=0.8)
    after = eastwards(mps=13.0, seconds=0.8, start_m=before[-1][0] + 13.0 * 0.5)

    drive(meter, [*before, *[None] * 7, *after])

    speed = meter.take(7)
    assert speed is not None
    assert speed.sustained_kmh is None


def test_a_jump_no_vehicle_could_make_starts_the_measurement_again() -> None:
    meter = SpeedMeter(SETTINGS)
    steady = eastwards(mps=10.0, seconds=1.5)
    # The id lands on another vehicle 20 m away, which then drives on at the same speed.
    elsewhere = eastwards(mps=10.0, seconds=1.5, start_m=steady[-1][0] + 20.0)

    drive(meter, [*steady, *elsewhere])
    speed = meter.take(7)

    assert speed is not None
    assert speed.sustained_kmh == pytest.approx(36.0, abs=0.1)


def test_nothing_is_measured_outside_the_mapped_area() -> None:
    meter = SpeedMeter(SETTINGS)

    drive(meter, eastwards(mps=13.0, seconds=3, start_m=70.0))
    speed = meter.take(7)

    assert speed is not None
    assert speed.sustained_kmh is None
    assert speed.stretch_kmh == {}


def test_a_stretch_average_is_entry_to_exit_over_the_time_taken() -> None:
    meter = SpeedMeter(SETTINGS)
    arriving = eastwards(mps=10.0, seconds=2.5)  # 5 m to 30 m: ten metres into the stretch
    waiting = [arriving[-1]] * (4 * FPS)
    leaving = eastwards(mps=10.0, seconds=2.5, start_m=arriving[-1][0])

    drive(meter, [*arriving, *waiting, *leaving[1:]])
    speed = meter.take(7)

    assert speed is not None
    # Twenty metres, from 20 m to 40 m, in one second, then four standing, then one more.
    assert speed.stretch_kmh == {"middle": pytest.approx(12.0, abs=0.8)}
    assert speed.sustained_kmh == pytest.approx(36.0, abs=0.1)


def test_a_track_that_only_clips_a_stretch_has_no_average() -> None:
    meter = SpeedMeter(SETTINGS)

    drive(meter, eastwards(mps=10.0, seconds=2.0, start_m=10.0))  # in at 20 m, gone by 30 m
    speed = meter.take(7)

    assert speed is not None
    assert speed.stretch_kmh == {}


def test_tracks_are_measured_side_by_side() -> None:
    meter = SpeedMeter(SETTINGS)
    for frame in range(3 * FPS):
        positions = {1: (5.0 + 6.0 * frame / FPS, 10.0), 2: (55.0 - 12.0 * frame / FPS, 30.0)}
        meter.update(observe(positions), at(frame))

    one, two = meter.take(1), meter.take(2)

    assert one is not None and two is not None
    assert one.sustained_kmh == pytest.approx(21.6, abs=0.1)
    assert two.sustained_kmh == pytest.approx(43.2, abs=0.1)


# The junction of test_geometry, ten pixels to the metre.
SPEED_SITE = copy.deepcopy(SITE) | {
    "ground_points": [
        {"pixel": [0, 0], "ground": [0, 150]},
        {"pixel": [2000, 0], "ground": [200, 150]},
        {"pixel": [2000, 1500], "ground": [200, 0]},
        {"pixel": [0, 1500], "ground": [0, 0]},
    ],
    "detectors": {
        "speed": {
            "stretches": {
                "box": {"polygon": [[800, 400], [1200, 400], [1200, 800], [800, 800]], "min_m": 30}
            },
            "limit_mph": 30,
            "flag_above_mph": 35,
        }
    },
}


def passage_at(pixels_per_frame: int):
    """The passage of a car driving up the image through the junction at a steady rate."""
    config = SiteConfig.model_validate(SPEED_SITE)
    assert config.detectors.speed is not None
    builder = PassageBuilder(config, "sha256:test", speeds=SpeedMeter(config.detectors.speed))
    scene = SceneGeometry(config)
    path = [(1000.0, float(y)) for y in range(1250, 130, -pixels_per_frame)]
    passages = []
    for frame in range(len(path) + LOST_FRAMES):
        tracks = sv.Detections.empty()
        if frame < len(path):
            x, y = path[frame]
            tracks = sv.Detections(
                xyxy=np.array([[x - 50, y - 60, x + 50, y]], dtype=np.float32),
                confidence=np.array([0.9], dtype=np.float32),
                class_id=np.array([CAR]),
                tracker_id=np.array([7]),
            )
        passages += builder.update(scene.observe(tracks, at(frame)), at(frame))
    (passage,) = passages
    return config, passage


def test_a_passage_carries_its_speed_and_where_it_was_held() -> None:
    _, passage = passage_at(pixels_per_frame=10)  # a metre a frame: 15 m/s

    assert passage.speed_kmh == pytest.approx(54.0, abs=0.1)
    speed = passage.flags["speed"]
    assert passage.first_seen < datetime.fromisoformat(speed["sustained_at"]) < passage.last_seen
    assert speed["stretch_kmh"] == {"box": pytest.approx(54.0, abs=0.1)}


def test_a_passage_without_a_meter_has_no_speed() -> None:
    config = SiteConfig.model_validate(SITE)
    builder = PassageBuilder(config, "sha256:test")
    scene = SceneGeometry(config)
    tracks = sv.Detections(
        xyxy=np.array([[950, 940, 1050, 1000]], dtype=np.float32),
        confidence=np.array([0.9], dtype=np.float32),
        class_id=np.array([CAR]),
        tracker_id=np.array([7]),
    )
    builder.update(scene.observe(tracks, at(0)), at(0))

    (passage,) = builder.flush(at(1))

    assert passage.speed_kmh is None
    assert passage.flags == {}


def test_a_passage_well_over_the_limit_is_a_speeding_event() -> None:
    config, passage = passage_at(pixels_per_frame=11)  # 59.4 km/h, 36.9 mph
    detector = Speeding(config, "sha256:test")

    (event,) = detector.passage_closed(passage)

    assert event.type == "speeding"
    assert event.passage_id == passage.id
    assert event.ts == datetime.fromisoformat(passage.flags["speed"]["sustained_at"])
    assert event.attrs == {
        "speed_mph": 36.9,
        "speed_kmh": passage.speed_kmh,
        "limit_mph": 30,
        "movement": "south->north",
    }
    assert detector.passage_closed(passage)[0].id == event.id


def test_a_passage_inside_the_margin_over_the_limit_is_not() -> None:
    config, passage = passage_at(pixels_per_frame=10)  # 54 km/h, 33.6 mph
    detector = Speeding(config, "sha256:test")

    assert passage.speed_kmh is not None and passage.speed_kmh / KMH_PER_MPH > 30
    assert detector.passage_closed(passage) == []
    assert detector.passage_closed(passage.model_copy(update={"speed_kmh": None})) == []
