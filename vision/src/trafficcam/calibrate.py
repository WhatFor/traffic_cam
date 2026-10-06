"""Calibrates the ground map: from points found on a satellite map to metres for site.yaml.

    python -m trafficcam.calibrate points --points FILE --frame FRAME --out IMAGE
    python -m trafficcam.calibrate fit --points FILE --frame FRAME --out DIR

FILE lists points on the road, each with where it is in the camera frame and its latitude
and longitude:

    points:
      box_nw: { pixel: [958, 776], lat: 53.1, lon: -2.2 }

`points` draws them, numbered, on the frame, for finding them on the map. `fit` prints the
`ground_points` block for site.yaml, in metres from the first point, and writes two images
to check the fit by eye. FILE says where the camera is, so it must never be committed.
"""

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict

from trafficcam.groundmap import GroundMap

GRID_M = 5
PLAN_PX_PER_M = 20
PLAN_MARGIN_M = 5
MARK = (0, 255, 255)


class _Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pixel: tuple[float, float] | None  # null where the camera cannot see the point
    lat: float | None  # null until read off the map
    lon: float | None


class _Points(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: dict[str, _Point]


@dataclass(frozen=True, slots=True)
class Located:
    name: str
    pixel: tuple[float, float] | None
    ground: tuple[float, float]  # metres east and north of the first located point


def metres_from(origin: tuple[float, float], lat: float, lon: float) -> tuple[float, float]:
    """Metres east and north of `origin`, a latitude and longitude. Exact enough for a junction."""
    mid = math.radians((origin[0] + lat) / 2)
    # The length of a degree on the WGS84 ellipsoid, at this latitude.
    north = 111132.92 - 559.82 * math.cos(2 * mid) + 1.175 * math.cos(4 * mid)
    east = 111412.84 * math.cos(mid) - 93.5 * math.cos(3 * mid)
    return (lon - origin[1]) * east, (lat - origin[0]) * north


def load(path: Path) -> dict[str, _Point]:
    return _Points.model_validate(yaml.safe_load(path.read_text())).points


def locate(points: dict[str, _Point]) -> list[Located]:
    """Every point that has been read off the map, in metres."""
    mapped = [(name, point) for name, point in points.items() if point.lat is not None]
    if not mapped:
        return []
    first = mapped[0][1]
    assert first.lat is not None and first.lon is not None
    origin = (first.lat, first.lon)
    located = []
    for name, point in mapped:
        assert point.lat is not None and point.lon is not None
        east, north = metres_from(origin, point.lat, point.lon)
        located.append(Located(name, point.pixel, (round(east, 2), round(north, 2))))
    return located


def site_yaml_block(located: list[Located]) -> str:
    lines = [
        "    ground_points:            # pixel in the frame; metres east and north of the first"
    ]
    for point in located:
        if point.pixel is not None:
            x, y = point.pixel
            east, north = point.ground
            where = f"pixel: [{x:g}, {y:g}], ground: [{east:.2f}, {north:.2f}]"
            lines.append(f"      - {{ {where} }}   # {point.name}")
    return "\n".join(lines)


def _mark(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, scale: float = 1.0) -> None:
    radius = 8 * scale
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=MARK, width=2)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            [(x + dx * 3, y + dy * 3), (x + dx * 2 * radius, y + dy * 2 * radius)],
            fill=MARK,
            width=2,
        )
    font = ImageFont.load_default(size=22 * scale)
    draw.text(
        (x + radius + 4, y - 3 * radius),
        text,
        font=font,
        fill=MARK,
        stroke_width=3,
        stroke_fill="black",
    )


def draw_points(frame: Image.Image, points: dict[str, _Point]) -> Image.Image:
    """The frame with every point the camera can see marked and numbered."""
    marked = frame.convert("RGB")
    draw = ImageDraw.Draw(marked)
    for number, (name, point) in enumerate(points.items(), start=1):
        if point.pixel is not None:
            _mark(draw, *point.pixel, f"{number} {name}")
    return marked


def plan_view(frame: Image.Image, ground_map: GroundMap, located: list[Located]) -> Image.Image:
    """The frame laid flat: north up, a grid line every 5 m. Straight kerbs should be straight."""
    ground = np.array([point.ground for point in located])
    west, south = ground.min(axis=0) - PLAN_MARGIN_M
    east, north = ground.max(axis=0) + PLAN_MARGIN_M
    size = (int((east - west) * PLAN_PX_PER_M), int((north - south) * PLAN_PX_PER_M))

    def to_plan(metres: np.ndarray) -> np.ndarray:
        return np.column_stack([(metres[:, 0] - west), (north - metres[:, 1])]) * PLAN_PX_PER_M

    corners = np.array([[west, north], [east, north], [east, south], [west, south]])
    matrix = cv2.getPerspectiveTransform(
        ground_map.to_pixels(corners).astype(np.float32), to_plan(corners).astype(np.float32)
    )
    plan = Image.fromarray(cv2.warpPerspective(np.asarray(frame.convert("RGB")), matrix, size))
    draw = ImageDraw.Draw(plan)
    for x in np.arange(math.ceil(west / GRID_M) * GRID_M, east, GRID_M):
        draw.line(
            [tuple(p) for p in to_plan(np.array([[x, south], [x, north]]))], fill=(255, 255, 255)
        )
    for y in np.arange(math.ceil(south / GRID_M) * GRID_M, north, GRID_M):
        draw.line(
            [tuple(p) for p in to_plan(np.array([[west, y], [east, y]]))], fill=(255, 255, 255)
        )
    for point in located:
        ((x, y),) = to_plan(np.array([point.ground]))
        _mark(draw, x, y, point.name, scale=0.6)
    return plan


def grid_view(frame: Image.Image, ground_map: GroundMap, located: list[Located]) -> Image.Image:
    """The frame with the 5 m grid drawn on the road, and the area the map is trusted in."""
    marked = frame.convert("RGB")
    draw = ImageDraw.Draw(marked)
    ground = np.array([point.ground for point in located if point.pixel is not None])
    west, south = np.floor(ground.min(axis=0) / GRID_M) * GRID_M
    east, north = np.ceil(ground.max(axis=0) / GRID_M) * GRID_M
    for x in np.arange(west, east + 1, GRID_M):
        line = ground_map.to_pixels(
            np.column_stack([np.full(50, x), np.linspace(south, north, 50)])
        )
        draw.line([tuple(p) for p in line], fill=(255, 255, 255), width=1)
    for y in np.arange(south, north + 1, GRID_M):
        line = ground_map.to_pixels(np.column_stack([np.linspace(west, east, 50), np.full(50, y)]))
        draw.line([tuple(p) for p in line], fill=(255, 255, 255), width=1)
    hull = [tuple(p) for p in ground_map.hull]
    draw.line([*hull, hull[0]], fill=(255, 200, 0), width=3)
    for point in located:
        ((x, y),) = ground_map.to_pixels(np.array([point.ground]))
        _mark(draw, x, y, point.name if point.pixel is not None else f"{point.name} (not seen)")
    return marked


def fit(points: dict[str, _Point], frame: Image.Image, out: Path) -> None:
    located = locate(points)
    seen = [(point, point.pixel) for point in located if point.pixel is not None]
    ground_map = GroundMap([pixel for _, pixel in seen], [point.ground for point, _ in seen])
    print("point                          error (m)")
    for (point, _), error in zip(seen, ground_map.errors_m, strict=True):
        print(f"  {point.name:28} {error:6.2f}")
    print(f"  root mean square             {float(np.sqrt((ground_map.errors_m**2).mean())):6.2f}")
    if len(seen) == 4:
        print("  Four points always fit exactly: there is nothing to check them against.")
    for point in located:
        if point.pixel is None:
            ((x, y),) = ground_map.to_pixels(np.array([point.ground]))
            print(
                f"  {point.name} is hidden from the camera; the map puts it at ({x:.0f}, {y:.0f})"
            )
    print("\nFor detectors.speed in site.yaml:\n")
    print(site_yaml_block(located))
    out.mkdir(parents=True, exist_ok=True)
    plan_view(frame, ground_map, located).save(out / "ground_plan.png")
    grid_view(frame, ground_map, located).save(out / "ground_grid.png")
    print(f"\nwrote {out / 'ground_plan.png'} and {out / 'ground_grid.png'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate the ground map from points found on a map."
    )
    parser.add_argument("command", choices=["points", "fit"])
    parser.add_argument("--points", type=Path, required=True, help="the points file")
    parser.add_argument("--frame", type=Path, required=True, help="full-resolution frame grab")
    parser.add_argument(
        "--out", type=Path, required=True, help="image to write, or a directory for fit"
    )
    args = parser.parse_args()
    points = load(args.points)
    with Image.open(args.frame) as frame:
        if args.command == "points":
            draw_points(frame, points).save(args.out)
            print(f"wrote {args.out}")
            return
        try:
            fit(points, frame, args.out)
        except ValueError as error:
            sys.exit(f"cannot fit: {error}")


if __name__ == "__main__":
    main()
