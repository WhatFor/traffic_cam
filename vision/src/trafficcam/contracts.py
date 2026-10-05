# Generated from contracts/trafficcam.v1.schema.json by `just gen-contracts`. Do not edit.

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class SignalState(StrEnum):
    red = "red"
    red_amber = "red_amber"
    green = "green"
    amber = "amber"
    unknown = "unknown"


class SignalSource(StrEnum):
    """
    observed: read from a visible signal head. inferred: estimated for a head that cannot be seen.
    """

    observed = "observed"
    inferred = "inferred"


class RoadUserClass(StrEnum):
    car = "car"
    truck = "truck"
    bus = "bus"
    motorcycle = "motorcycle"
    bicycle = "bicycle"
    person = "person"


class VisionState(StrEnum):
    online = "online"
    offline = "offline"


class Passage(BaseModel):
    """
    One completed road-user trip through the junction. Topic: trafficcam/v1/passages.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )
    schema_: Literal["passage/1"] = Field("passage/1", alias="schema")
    id: UUID
    ts: AwareDatetime
    """
    When the passage closed.
    """
    camera: str
    config_hash: str
    first_seen: AwareDatetime
    last_seen: AwareDatetime
    track_id: int | None
    class_: RoadUserClass | None = Field(..., alias="class")
    entry_zone: str | None
    exit_zone: str | None
    movement: str | None
    """
    For example 'south->east'.
    """
    stopline: str | None
    stopline_crossed_at: AwareDatetime | None
    signal_state_at_crossing: SignalState | None
    signal_source: SignalSource | None
    speed_kmh: float | None = Field(..., ge=0.0)
    flags: dict[str, Any]


class Event(BaseModel):
    """
    One detector event. Topic: trafficcam/v1/events/{type}.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )
    schema_: Literal["event/1"] = Field("event/1", alias="schema")
    id: UUID
    ts: AwareDatetime
    camera: str
    config_hash: str
    type: str = Field(..., pattern="^[a-z0-9_]+$")
    """
    Detector event type, for example 'box_junction_stop'.
    """
    detector_version: str
    passage_id: UUID | None
    track_id: int | None
    class_: RoadUserClass | None = Field(..., alias="class")
    confidence: float | None = Field(..., ge=0.0, le=1.0)
    attrs: dict[str, Any]
    """
    Detector-specific attributes.
    """
    clip_id: UUID | None


class SignalChange(BaseModel):
    """
    A signal head changed state. Topic: trafficcam/v1/signals/{head_id}, retained.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )
    schema_: Literal["signal_change/1"] = Field("signal_change/1", alias="schema")
    id: UUID
    ts: AwareDatetime
    camera: str
    config_hash: str
    head_id: str
    from_state: SignalState | None
    to_state: SignalState
    source: SignalSource
    confidence: float | None = Field(..., ge=0.0, le=1.0)


class Clip(BaseModel):
    """
    A clip file was closed. `id` is the clip id. Topic: trafficcam/v1/clips/{clip_id}.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )
    schema_: Literal["clip/1"] = Field("clip/1", alias="schema")
    id: UUID
    ts: AwareDatetime
    """
    When the file was closed.
    """
    camera: str
    config_hash: str
    event_id: UUID | None
    path: str
    keyframe_path: str | None
    """
    Still frame from the event moment.
    """
    started_at: AwareDatetime
    ended_at: AwareDatetime
    bytes: int = Field(..., ge=0)


class ClipCommand(BaseModel):
    """
    Manual clip trigger. Topic: trafficcam/v1/cmd/clip.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )
    schema_: Literal["clip_command/1"] = Field("clip_command/1", alias="schema")
    id: UUID
    ts: AwareDatetime
    camera: str
    reason: str


class Status(BaseModel):
    """
    Vision presence; 'offline' is the last will. Topic: trafficcam/v1/status/vision, retained.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )
    schema_: Literal["status/1"] = Field("status/1", alias="schema")
    id: UUID
    ts: AwareDatetime
    camera: str
    state: VisionState
