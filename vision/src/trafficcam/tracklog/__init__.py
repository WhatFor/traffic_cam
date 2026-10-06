"""Track logs: every frame's detections and tracks as JSON lines, for replays and tests.

A file holds one UTC hour. It starts with a `Header`, and gets another each time the
service restarts within that hour; every other line is a `FrameRecord`.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

import numpy as np
import supervision as sv
from pydantic import BaseModel, ConfigDict, Field

from trafficcam.contracts import RoadUserClass, SignalState
from trafficcam.geometry import Observation
from trafficcam.inference import CLASS_NAMES

SCHEMA = "trafficcam.tracklog.v1"

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in full-frame pixels


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Header(_Record):
    schema_: Literal["trafficcam.tracklog.v1"] = Field(alias="schema")
    camera: str
    config_hash: str
    started: datetime  # timestamp of the first frame that follows


class Detection(_Record):
    box: Box
    conf: float
    class_: RoadUserClass = Field(alias="class")


class Track(Detection):
    id: int
    zones: list[str]


class LineCrossing(_Record):
    id: int  # of the track
    line: str
    ts: datetime


class FrameRecord(_Record):
    frame: int
    ts: datetime
    detections: list[Detection]
    tracks: list[Track]
    crossings: list[LineCrossing]
    # Every signal head's state in this frame. Absent from logs made before signals were read.
    signals: dict[str, SignalState] = {}


def _exact(value: np.floating) -> float:
    # The shortest decimal that reads back as the same 32-bit float, so a replay
    # gives the tracker exactly what the live run saw.
    return float(str(np.float32(value)))


def _rows(detections: sv.Detections) -> list[dict[str, Any]]:
    confidence = detections.confidence if detections.confidence is not None else []
    class_id = detections.class_id if detections.class_id is not None else []
    return [
        {
            "box": [_exact(value) for value in box],
            "conf": _exact(score),
            "class": CLASS_NAMES[int(class_)],
        }
        for box, score, class_ in zip(detections.xyxy, confidence, class_id, strict=True)
    ]


def to_record(
    index: int,
    timestamp: datetime,
    detections: sv.Detections,
    observation: Observation,
    signals: Mapping[str, SignalState],
) -> FrameRecord:
    tracks = observation.tracks
    ids = tracks.tracker_id if tracks.tracker_id is not None else []
    return FrameRecord.model_validate(
        {
            "frame": index,
            "ts": timestamp,
            "detections": _rows(detections),
            "tracks": [
                row | {"id": int(track_id), "zones": observation.zones_of(position)}
                for position, (row, track_id) in enumerate(zip(_rows(tracks), ids, strict=True))
            ],
            "crossings": [
                {"id": crossing.track_id, "line": crossing.line, "ts": crossing.timestamp}
                for crossing in observation.crossings
            ],
            "signals": signals,
        }
    )
