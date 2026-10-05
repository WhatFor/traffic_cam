"""Brightness and sharpness of a frame."""

import cv2
import numpy as np
import numpy.typing as npt

from trafficcam.health.image_quality import measure


def uniform(level: int) -> npt.NDArray[np.uint8]:
    return np.full((120, 160, 3), level, dtype=np.uint8)


def detailed() -> npt.NDArray[np.uint8]:
    """A checkerboard of 8-pixel squares."""
    rows, columns = np.indices((120, 160)) // 8
    squares = ((rows + columns) % 2 * 255).astype(np.uint8)
    return np.dstack([squares] * 3)


def test_brightness_of_a_uniform_image_is_its_grey_level() -> None:
    assert measure(uniform(0)).brightness == 0
    assert measure(uniform(90)).brightness == 90


def test_a_flat_image_has_no_sharpness() -> None:
    assert measure(uniform(90)).sharpness == 0


def test_blurring_an_image_lowers_its_sharpness() -> None:
    image = detailed()
    blurred = np.asarray(cv2.GaussianBlur(image, (9, 9), 0), dtype=np.uint8)

    assert measure(blurred).sharpness < measure(image).sharpness / 2
