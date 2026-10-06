"""Site config: the model for config/site.yaml, its loader and its content hash."""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    model_validator,
)

from trafficcam.contracts import RoadUserClass

# Pixels in the camera.size frame.
Point = tuple[NonNegativeInt, NonNegativeInt]  # x, y
Rect = tuple[NonNegativeInt, NonNegativeInt, PositiveInt, PositiveInt]  # x, y, w, h

HomographyRow = tuple[float, float, float]


class ConfigError(Exception):
    """The site config could not be read or is not valid."""


class _Section(BaseModel):
    # Unknown keys are errors, so a typo cannot silently disable a setting.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Camera(_Section):
    id: str = Field(min_length=1)
    size: tuple[PositiveInt, PositiveInt]  # width, height
    fps: PositiveInt


class Inference(_Section):
    model: str
    threshold: float = Field(ge=0, le=1)
    classes: list[RoadUserClass]
    crops: list[Rect]
    merge_iou: float = Field(default=0.5, ge=0, le=1)


class Tracking(_Section):
    lost_s: PositiveFloat
    activation_threshold: float = Field(ge=0, le=1)
    high_confidence_threshold: float = Field(ge=0, le=1)
    min_consecutive_frames: PositiveInt
    min_iou: float = Field(ge=0, le=1)


# Degrees anticlockwise from image-right: 90 is up the image, 180 is to the left.
Heading = Annotated[float, Field(ge=0, lt=360)]


class Zone(_Section):
    polygon: list[Point] = Field(min_length=3)
    # Approach and exit zones name the arm of the junction they are on.
    role: Literal["approach", "exit"] | None = None
    arm: str | None = None
    # For an approach that shares its patch of road with another arm's traffic: the zone
    # is a track's entry only if the track is heading within this range, from the first
    # to the second anticlockwise, when it is first seen there.
    entry_heading: tuple[Heading, Heading] | None = None

    @model_validator(mode="after")
    def _check_role(self) -> Self:
        if self.role is not None and self.arm is None:
            raise ValueError("a zone with a role needs an arm")
        if self.entry_heading is not None and self.role != "approach":
            raise ValueError("entry_heading is only for approach zones")
        return self

    def accepts_heading(self, heading: float) -> bool:
        if self.entry_heading is None:
            return True
        low, high = self.entry_heading
        return low <= heading <= high if low <= high else heading >= low or heading <= high


class Line(_Section):
    points: tuple[Point, Point]
    # The way across that counts: towards the junction zone, or away from it.
    direction: Literal["inbound", "outbound"]
    # Observations on the far side before a crossing is believed.
    confirm_frames: PositiveInt = 2
    # A vehicle is tracked by the bottom of its box. Where traffic drives away from the
    # camera that is its rear, which crosses the line about this long after its front did.
    # The signal is read as it was that much earlier.
    lag_s: NonNegativeFloat = 0.0


class Movement(_Section):
    from_: str = Field(alias="from")
    to: str


LampColour = Literal["red", "amber", "green"]


class SignalHead(_Section):
    # The small square sampled at each lamp. A head the camera sees only partly lists
    # fewer than three: a pedestrian signal has red and green, and a head seen from the
    # side may show nothing but its green.
    lamps: dict[LampColour, Rect] = Field(min_length=1)
    # Stop lines and movements this head controls. Empty for a pedestrian signal.
    controls: list[str] = []


class BoxJunction(_Section):
    min_stationary_s: NonNegativeFloat
    exempt_movements: list[str]
    # How far a ground point may wander and still count as standing still.
    stationary_radius_px: PositiveFloat = 10.0


class BannedTurns(_Section):
    # Names from `movements`: each passage that makes one raises an event.
    movements: list[str]
    # Signal heads whose state is noted on the event.
    signal_heads: list[str] = []


class RedLight(_Section):
    # A crossing this soon after the signal turned red is let off.
    grace_s: NonNegativeFloat
    # Whether a crossing on amber raises an event of its own.
    amber_events: bool = True


class Speed(_Section):
    homography: tuple[HomographyRow, HomographyRow, HomographyRow]
    limit_mph: PositiveFloat


class Incident(_Section):
    decel_mps2: PositiveFloat
    notify_min_confidence: float = Field(ge=0, le=1)


class Detectors(_Section):
    box_junction: BoxJunction | None = None
    banned_turns: BannedTurns | None = None
    red_light: RedLight | None = None
    speed: Speed | None = None
    incident: Incident | None = None


class Clips(_Section):
    dir: Path
    pre_s: NonNegativeFloat
    post_s: PositiveFloat
    retention_days: PositiveInt
    max_gb: PositiveFloat


class SiteConfig(_Section):
    camera: Camera
    inference: Inference
    tracking: Tracking
    junction: str
    zones: dict[str, Zone]
    lines: dict[str, Line]
    movements: dict[str, Movement]
    signal_heads: dict[str, SignalHead]
    detectors: Detectors
    clips: Clips

    @model_validator(mode="after")
    def _check_geometry_and_references(self) -> Self:
        errors = [*self._geometry_errors(), *self._reference_errors()]
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def _geometry_errors(self) -> Iterable[str]:
        width, height = self.camera.size

        def outside(x: int, y: int) -> bool:
            return x > width or y > height

        for index, (x, y, w, h) in enumerate(self.inference.crops):
            if w != h:
                yield f"inference.crops[{index}] is not square"
            if outside(x + w, y + h):
                yield f"inference.crops[{index}] is outside the frame"
        for name, zone in self.zones.items():
            if any(outside(x, y) for x, y in zone.polygon):
                yield f"zones.{name} is outside the frame"
        for name, line in self.lines.items():
            if any(outside(x, y) for x, y in line.points):
                yield f"lines.{name} is outside the frame"
        for name, head in self.signal_heads.items():
            for colour, (x, y, w, h) in head.lamps.items():
                if outside(x + w, y + h):
                    yield f"signal_heads.{name}.lamps.{colour} is outside the frame"

    def lamp_regions(self) -> dict[str, Rect]:
        """Every lamp's square, keyed "head/colour", for a source to sample."""
        return {
            f"{name}/{colour}": rect
            for name, head in self.signal_heads.items()
            for colour, rect in head.lamps.items()
        }

    def heads_controlling(self, target: str) -> list[str]:
        return [name for name, head in self.signal_heads.items() if target in head.controls]

    def _reference_errors(self) -> Iterable[str]:
        if self.junction not in self.zones:
            yield f"junction names unknown zone '{self.junction}'"
        for name, movement in self.movements.items():
            for end, zone in (("from", movement.from_), ("to", movement.to)):
                if zone not in self.zones:
                    yield f"movements.{name}.{end} names unknown zone '{zone}'"
        for name, head in self.signal_heads.items():
            for target in head.controls:
                if target not in self.lines and target not in self.movements:
                    yield f"signal_heads.{name}.controls names unknown line or movement '{target}'"
        named = {}
        if self.detectors.box_junction is not None:
            named["box_junction.exempt_movements"] = self.detectors.box_junction.exempt_movements
        if self.detectors.banned_turns is not None:
            named["banned_turns.movements"] = self.detectors.banned_turns.movements
        for section, movements in named.items():
            for movement in movements:
                if movement not in self.movements:
                    yield f"detectors.{section} names unknown movement '{movement}'"
        if self.detectors.banned_turns is not None:
            for head in self.detectors.banned_turns.signal_heads:
                if head not in self.signal_heads:
                    yield f"detectors.banned_turns.signal_heads names unknown head '{head}'"


def load_site_config(path: Path) -> tuple[SiteConfig, str]:
    """Read and validate the site config. Returns it with the hash of the file's bytes."""
    try:
        raw = path.read_bytes()
        config = SiteConfig.model_validate(yaml.safe_load(raw))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigError(f"{path}: {error}") from error
    return config, "sha256:" + hashlib.sha256(raw).hexdigest()
