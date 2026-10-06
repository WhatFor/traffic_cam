"""Live frames from the Pi camera. Only importable on the Pi: picamera2 comes from apt."""

import time
from collections.abc import Iterator, Mapping

from picamera2 import MappedArray, Picamera2  # pyright: ignore[reportMissingImports]
from picamera2.encoders import H264Encoder  # pyright: ignore[reportMissingImports]
from picamera2.outputs import Output, PyavOutput  # pyright: ignore[reportMissingImports]

from trafficcam.clips import PacketRing
from trafficcam.sources import Frame
from trafficcam.sources.clock import sensor_time_to_utc
from trafficcam.sources.sampling import Rect, sample_yuv420

# Full field of view, 2x2 binned. Left to itself, picamera2 picks a cropped
# sensor mode when the output is small.
SENSOR_MODE = {"output_size": (2028, 1520), "bit_depth": 12}


class _RingOutput(Output):
    """Keeps the encoder's output in the ring. Called on the encoder's thread, so it only copies."""

    def __init__(self, ring: PacketRing) -> None:
        super().__init__()
        self._ring = ring

    def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=False) -> None:  # noqa: ANN001
        if self.recording and not audio and timestamp is not None:
            self._ring.append(bytes(frame), keyframe, timestamp)


class PiCameraSource:
    """Yields low-res frames and sends the main stream, H.264 encoded, to `live_url`.

    `regions`, in main-stream pixels, are sampled from the main stream of the same capture.
    The same encoded stream is kept in `ring`, if there is one, for clips to be cut from.
    """

    def __init__(
        self,
        *,
        main_size: tuple[int, int],
        lores_size: tuple[int, int],
        fps: int,
        bitrate: int,
        live_url: str,
        regions: Mapping[str, Rect] | None = None,
        ring: PacketRing | None = None,
    ) -> None:
        self._main_size = main_size
        self._lores_size = lores_size
        self._fps = fps
        self._bitrate = bitrate
        self._live_url = live_url
        self._regions = regions or {}
        self._ring = ring

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
            outputs = [PyavOutput(self._live_url, format="mpegts")]
            if self._ring is not None:
                outputs.append(_RingOutput(self._ring))
            camera.start_encoder(encoder, outputs, name="main")

            stride = camera.stream_configuration("main")["stride"]
            camera.start()
            index = 0
            while True:
                # One request, so the image and its metadata are the same frame.
                request = camera.capture_request()
                try:
                    image = request.make_array("lores")
                    sensor_ns = request.get_metadata()["SensorTimestamp"]
                    samples = {}
                    if self._regions:
                        # Mapped, not copied: only the few pixels of each region are read.
                        with MappedArray(request, "main", reshape=False, write=False) as mapped:
                            samples = sample_yuv420(
                                mapped.array, self._main_size, stride, self._regions
                            )
                finally:
                    request.release()
                boottime_ns, realtime_ns = (
                    time.clock_gettime_ns(time.CLOCK_BOOTTIME),
                    time.time_ns(),
                )
                timestamp = sensor_time_to_utc(sensor_ns, boottime_ns, realtime_ns)
                if self._ring is not None and not self._ring.has_origin:
                    # The encoder counts microseconds from the sensor time of its first frame.
                    first_us = encoder.firsttimestamp
                    if first_us is not None:
                        self._ring.set_origin(
                            sensor_time_to_utc(first_us * 1000, boottime_ns, realtime_ns)
                        )
                yield Frame(index, timestamp, image, samples)
                index += 1
