"""The ground map turns pixels into metres on the road, and says where it can be trusted."""

import numpy as np
import pytest

from trafficcam.groundmap import GroundMap, inside_convex

# A camera looking across the road from one side: strong perspective, nothing square.
GROUND_TO_PIXEL = np.array([[14.0, 6.0, 300.0], [-2.0, -9.0, 1200.0], [0.002, -0.004, 1.0]])
# Metres east and north: the corners of a 60 m by 40 m patch of road, and two points inside it.
MEASURED = np.array([[0, 0], [60, 0], [60, 40], [0, 40], [20, 10], [45, 30]], dtype=np.float64)


def pixels_of(ground: np.ndarray) -> np.ndarray:
    mapped = np.hstack([ground, np.ones((len(ground), 1))]) @ GROUND_TO_PIXEL.T
    return mapped[:, :2] / mapped[:, 2:3]


def a_map() -> GroundMap:
    return GroundMap(pixels_of(MEASURED).tolist(), MEASURED.tolist())


def test_the_fit_recovers_the_road_from_measured_points() -> None:
    ground_map = a_map()
    elsewhere = np.array([[10.0, 35.0], [55.0, 5.0], [30.0, 20.0]])

    # To a tenth of a millimetre, and of a pixel.
    assert np.allclose(ground_map.to_ground(pixels_of(elsewhere)), elsewhere, atol=1e-4)
    assert np.allclose(ground_map.to_pixels(elsewhere), pixels_of(elsewhere), atol=1e-4)
    assert ground_map.errors_m.max() < 1e-4


def test_the_map_is_trusted_only_among_its_points() -> None:
    ground_map = a_map()
    inside = pixels_of(np.array([[30.0, 20.0], [1.0, 1.0], [59.0, 39.0]]))
    outside = pixels_of(np.array([[-1.0, 20.0], [30.0, 41.0], [70.0, 50.0]]))

    assert ground_map.contains(inside).all()
    assert not ground_map.contains(outside).any()
    assert len(ground_map.hull) == 4


def test_points_that_disagree_are_tolerated_up_to_a_metre() -> None:
    off = MEASURED.copy()
    off[4] += (0.4, 0.0)

    close = GroundMap(pixels_of(MEASURED).tolist(), off.tolist())

    assert 0.1 < close.errors_m.max() < 0.4
    off[4] += (3.0, 0.0)
    with pytest.raises(ValueError, match="point 5 is"):
        GroundMap(pixels_of(MEASURED).tolist(), off.tolist())


def test_too_few_points_or_points_in_a_line_are_refused() -> None:
    with pytest.raises(ValueError, match="at least four"):
        GroundMap(pixels_of(MEASURED[:3]).tolist(), MEASURED[:3].tolist())
    line = np.array([[0.0, 0.0], [10.0, 5.0], [20.0, 10.0], [30.0, 15.0], [40.0, 20.2]])
    with pytest.raises(ValueError, match="nearly in a line"):
        GroundMap(pixels_of(line).tolist(), line.tolist())


def test_inside_convex_takes_corners_in_either_order() -> None:
    square = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
    points = np.array([[5.0, 5.0], [11.0, 5.0], [0.0, 0.0]])

    assert inside_convex(square, points).tolist() == [True, False, True]
    assert inside_convex(square[::-1], points).tolist() == [True, False, True]
