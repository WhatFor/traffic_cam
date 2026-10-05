"""Site config: the model for config/site.yaml, its loader and its content hash."""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Self

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


class Zone(_Section):
    polygon: list[Point] = Field(min_length=3)


class Line(_Section):
    points: tuple[Point, Point]
    direction: Literal["inbound", "outbound"]


class Movement(_Section):
    from_: str = Field(alias="from")
    to: str


class Lamps(_Section):
    red: Rect
    amber: Rect
    green: Rect


class SignalHead(_Section):
    lamps: Lamps
    controls: list[str]


class BoxJunction(_Section):
    min_stationary_s: NonNegativeFloat
    exempt_movements: list[str]


class RedLight(_Section):
    grace_s: NonNegativeFloat


class Speed(_Section):
    homography: tuple[HomographyRow, HomographyRow, HomographyRow]
    limit_mph: PositiveFloat


class Incident(_Section):
    decel_mps2: PositiveFloat
    notify_min_confidence: float = Field(ge=0, le=1)


class Detectors(_Section):
    box_junction: BoxJunction | None = None
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
            for colour, (x, y, w, h) in head.lamps:
                if outside(x + w, y + h):
                    yield f"signal_heads.{name}.lamps.{colour} is outside the frame"

    def _reference_errors(self) -> Iterable[str]:
        for name, movement in self.movements.items():
            for end, zone in (("from", movement.from_), ("to", movement.to)):
                if zone not in self.zones:
                    yield f"movements.{name}.{end} names unknown zone '{zone}'"
        for name, head in self.signal_heads.items():
            for target in head.controls:
                if target not in self.lines and target not in self.movements:
                    yield f"signal_heads.{name}.controls names unknown line or movement '{target}'"
        if self.detectors.box_junction is not None:
            for movement in self.detectors.box_junction.exempt_movements:
                if movement not in self.movements:
                    section = "detectors.box_junction.exempt_movements"
                    yield f"{section} names unknown movement '{movement}'"


def load_site_config(path: Path) -> tuple[SiteConfig, str]:
    """Read and validate the site config. Returns it with the hash of the file's bytes."""
    try:
        raw = path.read_bytes()
        config = SiteConfig.model_validate(yaml.safe_load(raw))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigError(f"{path}: {error}") from error
    return config, "sha256:" + hashlib.sha256(raw).hexdigest()
