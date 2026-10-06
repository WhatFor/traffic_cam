"""Frames decoded from a recorded clip, for replays and tests."""

from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from pathlib import Path

import av
import numpy as np

from trafficcam.sources import Frame
from trafficcam.sources.clock import EPOCH
from trafficcam.sources.sampling import Rect, sample_rgb


class VideoFileSource:
    """Yields a clip's frames as fast as they decode, resized to `size`.

    `regions` are sampled from each frame at the clip's own resolution, before resizing.
    """

    def __init__(
        self,
        path: Path,
        *,
        size: tuple[int, int],
        start: datetime = EPOCH,
        regions: Mapping[str, Rect] | None = None,
    ) -> None:
        self._path = path
        self._size = size
        self._start = start
        self._regions = regions or {}

    def frames(self) -> Iterator[Frame]:
        width, height = self._size
        with av.open(str(self._path)) as container:
            first_time: float | None = None
            for index, decoded in enumerate(container.decode(video=0)):
                if decoded.time is None:
                    raise ValueError(f"{self._path}: frame {index} has no timestamp")
                if first_time is None:
                    first_time = decoded.time
                image = decoded.to_ndarray(width=width, height=height, format="rgb24")
                samples = {}
                if self._regions:
                    full = np.asarray(decoded.to_ndarray(format="rgb24"), dtype=np.uint8)
                    samples = sample_rgb(full, self._regions)
                yield Frame(
                    index=index,
                    timestamp=self._start + timedelta(seconds=decoded.time - first_time),
                    image=np.asarray(image, dtype=np.uint8),
                    samples=samples,
                )
