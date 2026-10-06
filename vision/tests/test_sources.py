"""Frame sources yield frames with the right size, colour order and timestamps."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import av
import numpy as np
import pytest

from trafficcam.sources.clock import EPOCH, sensor_time_to_utc
from trafficcam.sources.video_file import VideoFileSource

FPS = 10
FRAME_COUNT = 5
RED = (220, 30, 30)


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=FPS)
        assert isinstance(stream, av.VideoStream)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        image = np.full((48, 64, 3), RED, dtype=np.uint8)
        for _ in range(FRAME_COUNT):
            container.mux(stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
        container.mux(stream.encode())
    return path


def test_video_file_frames_are_resized_rgb(clip: Path) -> None:
    frames = list(VideoFileSource(clip, size=(32, 24)).frames())

    assert [frame.index for frame in frames] == list(range(FRAME_COUNT))
    for frame in frames:
        assert frame.image.shape == (24, 32, 3)
        assert frame.image.dtype == np.uint8
        assert np.allclose(frame.image.mean(axis=(0, 1)), RED, atol=20)


def test_video_file_timestamps_follow_the_clip(clip: Path) -> None:
    start = datetime(2026, 10, 4, 13, 5, tzinfo=UTC)

    timestamps = [
        frame.timestamp for frame in VideoFileSource(clip, size=(32, 24), start=start).frames()
    ]

    assert timestamps[0] == start
    for earlier, later in zip(timestamps, timestamps[1:], strict=False):
        assert later - earlier == pytest.approx(
            timedelta(seconds=1 / FPS), abs=timedelta(milliseconds=1)
        )


def test_video_file_starts_at_the_epoch_by_default(clip: Path) -> None:
    first = next(VideoFileSource(clip, size=(32, 24)).frames())

    assert first.timestamp == EPOCH


def test_sensor_time_to_utc_offsets_the_boot_clock() -> None:
    realtime_ns = 1_791_000_000_000_000_000
    boottime_ns = 5_000_000_000

    # A frame exposed half a second before the two clocks were read.
    captured = sensor_time_to_utc(boottime_ns - 500_000_000, boottime_ns, realtime_ns)

    assert captured == EPOCH + timedelta(seconds=1_791_000_000) - timedelta(milliseconds=500)


def test_video_file_samples_regions_at_the_clips_own_size(clip: Path) -> None:
    regions = {"lamp": (10, 10, 5, 5)}

    frames = list(VideoFileSource(clip, size=(32, 24), regions=regions).frames())

    for frame in frames:
        assert frame.samples["lamp"] == pytest.approx(RED, abs=20)


def test_video_file_samples_nothing_unless_asked(clip: Path) -> None:
    assert next(VideoFileSource(clip, size=(32, 24)).frames()).samples == {}
