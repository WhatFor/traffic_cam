"""Clip files: encoded packets into an MP4, and a still frame out of it."""

import contextlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av

from trafficcam.clips import Packet

MICROSECONDS = Fraction(1, 1_000_000)
PART = ".part"
STILL_QUALITY = 90


@dataclass(frozen=True, slots=True)
class VideoStream:
    """What the encoder produces, as far as a container needs to know."""

    width: int
    height: int
    fps: int
    codec: str = "h264"


class ClipFile:
    """An MP4 being written. It has its final name only once it is complete."""

    def __init__(self, path: Path, stream: VideoStream) -> None:
        self.path = path
        self._part = path.with_name(path.name + PART)
        # faststart puts the index first, so the file plays over HTTP without being fetched whole.
        self._container = av.open(
            str(self._part), "w", format="mp4", options={"movflags": "faststart"}
        )
        self._stream = self._container.add_stream(
            stream.codec, rate=stream.fps, width=stream.width, height=stream.height
        )
        self.first_pts: int | None = None
        self.last_pts: int | None = None

    def write(self, packets: list[Packet]) -> None:
        for held in packets:
            if self.first_pts is None:
                self.first_pts = held.pts_us
            packet = av.Packet(held.data)
            packet.pts = packet.dts = held.pts_us - self.first_pts
            packet.time_base = MICROSECONDS
            packet.is_keyframe = held.keyframe
            packet.stream = self._stream
            self._container.mux(packet)
            self.last_pts = held.pts_us

    def close(self) -> None:
        self._container.close()
        self._part.rename(self.path)

    def abandon(self) -> None:
        with contextlib.suppress(Exception):
            self._container.close()
        self._part.unlink(missing_ok=True)


def save_still(clip: Path, offset_s: float, still: Path) -> None:
    """Save the frame `offset_s` into the clip as a JPEG, at the clip's own size."""
    with av.open(str(clip)) as container:
        # Lands on the keyframe before the moment; decode forward from there.
        container.seek(int(offset_s * av.time_base))
        chosen = None
        for frame in container.decode(video=0):
            chosen = frame
            if frame.time is not None and frame.time >= offset_s:
                break
        if chosen is None:
            raise ValueError(f"{clip} has no frames")
        chosen.to_image().save(still, quality=STILL_QUALITY)
