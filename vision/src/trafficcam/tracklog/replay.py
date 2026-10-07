"""Reads track logs back, and replays the detections in them through the pipeline."""

import sys
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import supervision as sv
from pydantic import TypeAdapter, ValidationError

from trafficcam.inference import COCO_CLASS_IDS
from trafficcam.signals import Reading
from trafficcam.sources import Frame, Rgb
from trafficcam.tracklog import Detection, FrameRecord, Header

_LINE: TypeAdapter[Header | FrameRecord] = TypeAdapter(Header | FrameRecord)
# More than five minutes of frames, at about 3 kB each.
_TAIL_BYTES = 24_000_000
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


def recent_lamps(
    directory: Path, now: datetime, span: timedelta, stale: timedelta
) -> list[tuple[datetime, Mapping[str, Rgb]]]:
    """The lamp colours recorded over the last `span`, oldest first, for starting the signal
    reading where it left off. None at all if the newest is older than `stale`: the light
    will have changed. Only the end of the newest files is read, not the hours before it.
    """
    frames: list[tuple[datetime, Mapping[str, Rgb]]] = []
    for path in sorted(directory.glob("*.jsonl"))[-2:]:
        with path.open("rb") as file:
            file.seek(max(0, file.seek(0, 2) - _TAIL_BYTES))
            # The first line is cut short wherever the reading began, and the last may be too.
            lines = file.read().split(b"\n")[1:-1]
        for line in lines:
            if b'"lamps":{"' not in line:
                continue
            try:
                record = _LINE.validate_json(line)
            except ValidationError:
                continue
            if isinstance(record, FrameRecord) and now - record.ts <= span:
                frames.append((record.ts, record.lamps))
    return frames if frames and now - frames[-1][0] <= stale else []


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
        self._showing: dict[str, Reading] = {}

    def frames(self) -> Iterator[Frame]:
        noted = False
        for record in read_track_log(self._paths):
            if isinstance(record, Header):
                if record.config_hash != self._config_hash and not noted:
                    noted = True
                    print("track log was recorded with a different config", file=sys.stderr)
                continue
            self._current = record
            yield Frame(
                index=record.frame, timestamp=record.ts, image=NO_IMAGE, samples=record.lamps
            )

    def detect(self, frame: Frame) -> sv.Detections:
        return to_detections(self._record_of(frame).detections)

    def read(self, frame: Frame) -> Mapping[str, Reading]:
        """The signal states recorded for this frame, each dated from when it first showed."""
        for head, state in self._record_of(frame).signals.items():
            if head not in self._showing or self._showing[head].state != state:
                self._showing[head] = Reading(state, frame.timestamp)
        return self._showing

    def _record_of(self, frame: Frame) -> FrameRecord:
        if self._current is None or self._current.frame != frame.index:
            raise ValueError(f"frame {frame.index} is not the one being replayed")
        return self._current
