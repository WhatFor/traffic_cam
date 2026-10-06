"""Frame sources: where the pipeline's frames come from."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import numpy as np
import numpy.typing as npt

Rgb = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Frame:
    index: int
    timestamp: datetime  # UTC, when the sensor captured it
    image: npt.NDArray[np.uint8]  # RGB, height x width x 3
    # Mean colour of each region the source was asked to sample, taken from the
    # full-resolution stream: the image above is too small to read signal lamps from.
    samples: Mapping[str, Rgb] = field(default_factory=dict)


class FrameSource(Protocol):
    def frames(self) -> Iterator[Frame]: ...
