"""Frame sources: where the pipeline's frames come from."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True, slots=True)
class Frame:
    index: int
    timestamp: datetime  # UTC, when the sensor captured it
    image: npt.NDArray[np.uint8]  # RGB, height x width x 3


class FrameSource(Protocol):
    def frames(self) -> Iterator[Frame]: ...
