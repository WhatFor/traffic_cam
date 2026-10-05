"""Live frames from the Pi camera. Only importable on the Pi: picamera2 comes from apt."""

import time
from collections.abc import Iterator

from picamera2 import Picamera2  # pyright: ignore[reportMissingImports]
from picamera2.encoders import H264Encoder  # pyright: ignore[reportMissingImports]
from picamera2.outputs import PyavOutput  # pyright: ignore[reportMissingImports]

from trafficcam.sources import Frame
from trafficcam.sources.clock import sensor_time_to_utc

# Full field of view, 2x2 binned. Left to itself, picamera2 picks a cropped
# sensor mode when the output is small.
SENSOR_MODE = {"output_size": (2028, 1520), "bit_depth": 12}


class PiCameraSource:
    """Yields low-res frames and sends the main stream, H.264 encoded, to `live_url`."""

    def __init__(
        self,
        *,
        main_size: tuple[int, int],
        lores_size: tuple[int, int],
        fps: int,
        bitrate: int,
        live_url: str,
    ) -> None:
        self._main_size = main_size
        self._lores_size = lores_size
        self._fps = fps
        self._bitrate = bitrate
        self._live_url = live_url

    def frames(self) -> Iterator[Frame]:
        with Picamera2() as camera:
            config = camera.create_video_configuration(
                main={"size": self._main_size, "format": "YUV420"},
                # picamera2's "BGR888" is RGB byte order.
                lores={"size": self._lores_size, "format": "BGR888"},
                sensor=SENSOR_MODE,
                controls={"FrameRate": self._fps},
            )
            camera.configure(config)

            # The Pi 5 has no hardware H.264 encoder, so this is libx264 on the CPU.
            # It must stay the only encode. One keyframe per second keeps viewer start-up short.
            encoder = H264Encoder(bitrate=self._bitrate, iperiod=self._fps, framerate=self._fps)
            camera.start_encoder(encoder, PyavOutput(self._live_url, format="mpegts"), name="main")

            camera.start()
            index = 0
            while True:
                # One request, so the image and its metadata are the same frame.
                request = camera.capture_request()
                try:
                    image = request.make_array("lores")
                    sensor_ns = request.get_metadata()["SensorTimestamp"]
                finally:
                    request.release()
                timestamp = sensor_time_to_utc(
                    sensor_ns, time.clock_gettime_ns(time.CLOCK_BOOTTIME), time.time_ns()
                )
                yield Frame(index, timestamp, image)
                index += 1
