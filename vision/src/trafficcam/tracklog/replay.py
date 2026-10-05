"""Reads track logs back, and replays the detections in them through the pipeline."""

import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import supervision as sv
from pydantic import TypeAdapter, ValidationError

from trafficcam.inference import COCO_CLASS_IDS
from trafficcam.sources import Frame
from trafficcam.tracklog import Detection, FrameRecord, Header

_LINE: TypeAdapter[Header | FrameRecord] = TypeAdapter(Header | FrameRecord)
NO_IMAGE = np.empty((0, 0, 3), dtype=np.uint8)


def read_track_log(paths: Sequence[Path]) -> Iterator[Header | FrameRecord]:
    for path in paths:
        with path.open(encoding="utf-8") as file:
            for number, line in enumerate(file, start=1):
                # A file copied while it is being written can end part-way through a line.
                if not line.endswith("\n"):
                    break
                try:
                    yield _LINE.validate_json(line)
                except ValidationError as error:
                    raise ValueError(f"{path}:{number}: {error}") from error


def to_detections(recorded: Sequence[Detection]) -> sv.Detections:
    return sv.Detections(
        xyxy=np.array([row.box for row in recorded], dtype=np.float32).reshape(-1, 4),
        confidence=np.array([row.conf for row in recorded], dtype=np.float32),
        class_id=np.array([COCO_CLASS_IDS[row.class_] for row in recorded], dtype=np.int_),
    )


class TrackLogReplay:
    """A recorded run, as both the frame source and the inference backend of a replay.

    Frames carry no image. Only the recorded detections are used: tracking and everything
    after it run again, with the current config.
    """

    def __init__(self, paths: Sequence[Path], config_hash: str) -> None:
        self._paths = paths
        self._config_hash = config_hash
        self._current: FrameRecord | None = None

    def frames(self) -> Iterator[Frame]:
        noted = False
        for record in read_track_log(self._paths):
            if isinstance(record, Header):
                if record.config_hash != self._config_hash and not noted:
                    noted = True
                    print("track log was recorded with a different config", file=sys.stderr)
                continue
            self._current = record
            yield Frame(index=record.frame, timestamp=record.ts, image=NO_IMAGE)

    def detect(self, frame: Frame) -> sv.Detections:
        if self._current is None or self._current.frame != frame.index:
            raise ValueError(f"frame {frame.index} is not the one being replayed")
        return to_detections(self._current.detections)
