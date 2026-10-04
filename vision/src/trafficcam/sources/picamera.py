"""Live frames from the Pi camera. Only importable on the Pi: picamera2 comes from apt."""

from collections.abc import Iterator

import numpy as np
import numpy.typing as npt
from picamera2 import Picamera2  # pyright: ignore[reportMissingImports]

# Full field of view, 2x2 binned. Left to itself, picamera2 picks a cropped
# sensor mode when the output is small.
SENSOR_MODE = {"output_size": (2028, 1520), "bit_depth": 12}


class PiCameraSource:
    def __init__(self, size: tuple[int, int], fps: int) -> None:
        self._size = size
        self._fps = fps

    def frames(self) -> Iterator[npt.NDArray[np.uint8]]:
        """Yield RGB frames of shape (height, width, 3) until the process stops."""
        with Picamera2() as camera:
            config = camera.create_video_configuration(
                # picamera2's "BGR888" is RGB byte order.
                main={"size": self._size, "format": "BGR888"},
                sensor=SENSOR_MODE,
                controls={"FrameRate": self._fps},
            )
            camera.configure(config)
            camera.start()
            while True:
                yield camera.capture_array("main")
