"""Image quality: how bright and how sharp a frame is."""

from typing import NamedTuple

import cv2
import numpy as np
import numpy.typing as npt


class ImageQuality(NamedTuple):
    brightness: float  # mean grey level, 0 to 255
    # Variance of the Laplacian: higher is sharper, but it depends on the scene, so it
    # is only comparable between frames of the same view in similar light.
    sharpness: float


def measure(image: npt.NDArray[np.uint8]) -> ImageQuality:
    """Measure an RGB image."""
    grey = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    return ImageQuality(
        brightness=float(grey.mean()),
        sharpness=float(cv2.Laplacian(grey, cv2.CV_32F).var()),
    )
