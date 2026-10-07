"""Site config: the model for config/site.yaml, its loader and its content hash."""

import hashlib
from collections.abc import Iterable, Mapping
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
from trafficcam.groundmap import GroundMap

# Pixels in the camera.size frame.
Point = tuple[NonNegativeInt, NonNegativeInt]  # x, y
Rect = tuple[NonNegativeInt, NonNegativeInt, PositiveInt, PositiveInt]  # x, y, w, h


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


class GroundPoint(_Section):
    """A point on the road surface that has been found both in the image and on a map."""

    pixel: tuple[float, float]
    # Metres east and north of whichever point was taken as the origin.
    ground: tuple[float, float]


class Stretch(_Section):
    """A length of road over which an average speed is taken."""

    polygon: list[Point] = Field(min_length=3)
    # A track must cover at least this much of it, entry to exit, for its average to count.
    min_m: PositiveFloat


class Speed(_Section):
    # A passage's speed is the fastest it held for this long.
    sustained_s: PositiveFloat = 1.0
    stretches: dict[str, Stretch] = {}
    limit_mph: PositiveFloat
    # A passage faster than this raises an event. Above the limit by more than the
    # measurement can be wrong by.
    flag_above_mph: PositiveFloat

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.flag_above_mph < self.limit_mph:
            raise ValueError("flag_above_mph is below limit_mph")
        return self


class NearMiss(_Section):
    # Two road users on crossing paths that reach the same spot less than this apart.
    pet_max_s: PositiveFloat
    # Paths at a smaller angle are one following the other, or two streams merging.
    min_angle_deg: float = Field(default=45, gt=0, lt=180)
    # Both must be going at least this fast at the spot.
    min_speed_mph: PositiveFloat = 3.0


class Incident(_Section):
    # Crossing paths this close in time may have touched. Alone that is a near miss.
    contact_s: PositiveFloat
    # ...unless one of the two then stands near the spot for this long.
    standstill_after_s: PositiveFloat
    # A vehicle standing this long where traffic does not queue is a candidate by itself.
    lone_standstill_s: PositiveFloat
    # How far a ground point may wander and still count as standing still.
    stationary_radius_px: PositiveFloat = 10.0
    # A candidate at least this sure raises an event; one this sure is worth telling someone.
    min_confidence: float = Field(ge=0, le=1)
    notify_min_confidence: float = Field(ge=0, le=1)


class Detectors(_Section):
    box_junction: BoxJunction | None = None
    banned_turns: BannedTurns | None = None
    red_light: RedLight | None = None
    speed: Speed | None = None
    near_miss: NearMiss | None = None
    incident: Incident | None = None


class ClipLength(_Section):
    pre_s: NonNegativeFloat | None = None
    post_s: PositiveFloat | None = None
    # Only an event with every one of these attributes at or above the value triggers a
    # clip, and every one of those in `max` at or below.
    min: dict[str, float] = {}
    max: dict[str, float] = {}


class Clips(_Section):
    dir: Path
    # How much is kept before and after a trigger, unless the trigger's event type says otherwise.
    pre_s: NonNegativeFloat
    post_s: PositiveFloat
    # Encoded video held in memory. An event is raised when its passage closes, which can be
    # a minute after the moment it describes, and the clip has to reach back to that moment.
    buffer_s: PositiveFloat = 90
    # Triggers that overlap extend a clip, up to this length.
    max_s: PositiveFloat = 300
    retention_days: PositiveInt
    max_gb: PositiveFloat
    # The event types that trigger a clip, each with its own lengths if it gives them.
    events: dict[str, ClipLength] = {}

    def wants(self, event_type: str, attrs: Mapping[str, object]) -> bool:
        """Whether an event of this type, with these attributes, is to have a clip."""
        rule = self.events.get(event_type)
        if rule is None:
            return False

        def within(limits: Mapping[str, float], sign: int) -> bool:
            return all(
                isinstance(value := attrs.get(name), int | float) and sign * value >= sign * limit
                for name, limit in limits.items()
            )

        return within(rule.min, 1) and within(rule.max, -1)

    def lengths(self, event_type: str | None = None) -> tuple[float, float]:
        """Seconds kept before and after a trigger of this event type, or a manual one."""
        own = self.events.get(event_type, ClipLength()) if event_type else ClipLength()
        return (
            self.pre_s if own.pre_s is None else own.pre_s,
            self.post_s if own.post_s is None else own.post_s,
        )

    @model_validator(mode="after")
    def _check_lengths(self) -> Self:
        for name in (None, *self.events):
            pre_s, post_s = self.lengths(name)
            where = f"events.{name}" if name else "the default"
            if pre_s >= self.buffer_s:
                raise ValueError(f"{where}: pre_s must be less than buffer_s")
            if pre_s + post_s > self.max_s:
                raise ValueError(f"{where}: pre_s plus post_s is more than max_s")
        return self


class SiteConfig(_Section):
    camera: Camera
    inference: Inference
    tracking: Tracking
    junction: str
    zones: dict[str, Zone]
    lines: dict[str, Line]
    movements: dict[str, Movement]
    signal_heads: dict[str, SignalHead]
    # The road in metres, for the detectors that need distances. Empty where it has not
    # been measured.
    ground_points: list[GroundPoint] = []
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

    def ground_map(self) -> GroundMap | None:
        """The map of the road fitted to `ground_points`, if there are any."""
        if not self.ground_points:
            return None
        return GroundMap(
            [point.pixel for point in self.ground_points],
            [point.ground for point in self.ground_points],
        )

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
        try:
            self.ground_map()
        except ValueError as error:
            yield f"ground_points: {error}"
        for name in ("speed", "near_miss", "incident"):
            if getattr(self.detectors, name) is not None and not self.ground_points:
                yield f"detectors.{name} needs ground_points"
        if self.detectors.incident is not None and self.detectors.near_miss is None:
            yield "detectors.incident needs detectors.near_miss, whose conflicts it starts from"


def load_site_config(path: Path) -> tuple[SiteConfig, str]:
    """Read and validate the site config. Returns it with the hash of the file's bytes."""
    try:
        raw = path.read_bytes()
        config = SiteConfig.model_validate(yaml.safe_load(raw))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigError(f"{path}: {error}") from error
    return config, "sha256:" + hashlib.sha256(raw).hexdigest()
