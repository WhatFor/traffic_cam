"""The road surface in metres: a mapping from image pixels, fitted from measured points."""

from collections.abc import Sequence

import cv2
import numpy as np
import numpy.typing as npt

Points = npt.NDArray[np.float64]  # one row per point: x, y

# Above this, a measured point disagrees with the others too much to trust the fit.
MAX_ERROR_M = 1.0
# Points nearly in a line fix the mapping along the line and leave it loose across it.
MIN_SPREAD = 0.05


class GroundMap:
    """Maps the camera's view of the road to a flat plan of it, and back.

    Fitted to points whose positions are known both in the image and on the ground. It is
    trusted only among those points: `contains` says whether a pixel lies within them.
    """

    def __init__(
        self, pixels: Sequence[Sequence[float]], ground: Sequence[Sequence[float]]
    ) -> None:
        image_points = np.asarray(pixels, dtype=np.float64)
        ground_points = np.asarray(ground, dtype=np.float64)
        if len(image_points) < 4 or len(image_points) != len(ground_points):
            raise ValueError(
                "at least four points are needed, each with a pixel and a ground position"
            )
        for name, points in (("pixel", image_points), ("ground", ground_points)):
            if _spread(points) < MIN_SPREAD:
                raise ValueError(f"the {name} positions are nearly in a line")
        matrix, _ = cv2.findHomography(image_points, ground_points)
        if matrix is None:
            raise ValueError("no mapping fits the points")
        self._to_ground = np.asarray(matrix, dtype=np.float64)
        self._to_pixels = np.linalg.inv(self._to_ground)
        # In order around the outside, as the corners of the area that is trusted.
        self.hull: Points = (
            cv2.convexHull(image_points.astype(np.float32)).reshape(-1, 2).astype(np.float64)
        )
        self.errors_m: npt.NDArray[np.float64] = np.linalg.norm(
            self.to_ground(image_points) - ground_points, axis=1
        )
        worst = int(self.errors_m.argmax())
        if self.errors_m[worst] > MAX_ERROR_M:
            raise ValueError(
                f"point {worst + 1} is {self.errors_m[worst]:.1f} m from where the others put it"
            )

    def to_ground(self, pixels: Points) -> Points:
        """Metres on the road for each pixel position."""
        return _apply(self._to_ground, pixels)

    def to_pixels(self, ground: Points) -> Points:
        return _apply(self._to_pixels, ground)

    def contains(self, pixels: Points) -> npt.NDArray[np.bool_]:
        """Which pixel positions lie among the measured points."""
        return inside_convex(self.hull, pixels)


def inside_convex(polygon: Points, points: Points) -> npt.NDArray[np.bool_]:
    """Which points are inside a convex polygon whose corners are given in order."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    edges = np.roll(polygon, -1, axis=0) - polygon
    offsets = points[:, None, :] - polygon[None, :, :]
    side = edges[None, :, 0] * offsets[:, :, 1] - edges[None, :, 1] * offsets[:, :, 0]
    return np.all(side >= 0, axis=1) | np.all(side <= 0, axis=1)


def _apply(matrix: npt.NDArray[np.float64], points: Points) -> Points:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    mapped = np.hstack([points, np.ones((len(points), 1))]) @ matrix.T
    return mapped[:, :2] / mapped[:, 2:3]


def _spread(points: Points) -> float:
    """How far the points are from lying in a line: 0 for a line, 1 for an even scatter."""
    values = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    return float(values[1] / values[0]) if values[0] > 0 else 0.0
