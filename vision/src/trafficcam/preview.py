"""Draws the site config's geometry over a frame grab, to check coordinates by eye."""

import argparse
import math
from itertools import cycle
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from trafficcam.config import SiteConfig, load_site_config
from trafficcam.geometry import junction_centre
from trafficcam.geometry.lines import counted_normal

GRID_PX = 100
GRID_COLOUR = (255, 255, 255, 70)
CROP_COLOUR = (0, 220, 255)
LINE_COLOUR = (0, 255, 0)
LAMP_COLOUR = (255, 140, 0)
SPEED_COLOUR = (255, 255, 0)
ZONE_COLOURS = [(255, 0, 255), (255, 80, 80), (80, 160, 255), (255, 200, 0), (160, 100, 255)]

Colour = tuple[int, int, int]


def render(frame: Image.Image, config: SiteConfig) -> Image.Image:
    """Return a copy of `frame` with a coordinate grid and every configured shape drawn on it."""
    if frame.size != config.camera.size:
        raise ValueError(f"frame is {frame.size}, but camera.size is {config.camera.size}")

    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=22)

    def label(x: float, y: float, text: str, colour: Colour) -> None:
        draw.text((x, y), text, font=font, fill=colour, stroke_width=3, stroke_fill="black")

    width, height = frame.size
    for x in range(0, width, GRID_PX):
        draw.line([(x, 0), (x, height)], fill=GRID_COLOUR)
        label(x + 3, 2, str(x), (255, 255, 255))
    for y in range(GRID_PX, height, GRID_PX):
        draw.line([(0, y), (width, y)], fill=GRID_COLOUR)
        label(3, y + 2, str(y), (255, 255, 255))

    for index, (x, y, w, h) in enumerate(config.inference.crops):
        draw.rectangle([x, y, x + w, y + h], outline=CROP_COLOUR, width=4)
        label(x + 8, y + h - 34, f"crop {index}", CROP_COLOUR)

    for (name, zone), colour in zip(config.zones.items(), cycle(ZONE_COLOURS), strict=False):
        draw.polygon(zone.polygon, fill=(*colour, 60), outline=colour, width=3)
        centre_x = sum(x for x, _ in zone.polygon) / len(zone.polygon)
        centre_y = sum(y for _, y in zone.polygon) / len(zone.polygon)
        label(centre_x - 6 * len(name), centre_y - 12, name, colour)

    junction = junction_centre(config)
    for name, line in config.lines.items():
        (x1, y1), (x2, y2) = line.points
        draw.line([(x1, y1), (x2, y2)], fill=LINE_COLOUR, width=4)
        # An arrow across the line, pointing the way a crossing counts.
        normal_x, normal_y = counted_normal(line, junction)
        tail = ((x1 + x2) / 2 - 30 * normal_x, (y1 + y2) / 2 - 30 * normal_y)
        tip = ((x1 + x2) / 2 + 30 * normal_x, (y1 + y2) / 2 + 30 * normal_y)
        draw.line([tail, tip], fill=LINE_COLOUR, width=4)
        angle = math.atan2(normal_y, normal_x)
        for side in (-0.5, 0.5):
            barb = (tip[0] - 18 * math.cos(angle + side), tip[1] - 18 * math.sin(angle + side))
            draw.line([tip, barb], fill=LINE_COLOUR, width=4)
        label(x2 + 10, y2 - 12, f"{name} ({line.direction})", LINE_COLOUR)

    for name, head in config.signal_heads.items():
        # The lamp squares are a few pixels across, so the head is boxed as a whole.
        rects = list(head.lamps.values())
        left, top = min(x for x, _, _, _ in rects) - 6, min(y for _, y, _, _ in rects) - 6
        right = max(x + w for x, _, w, _ in rects) + 6
        bottom = max(y + h for _, y, _, h in rects) + 6
        draw.rectangle([left, top, right, bottom], outline=LAMP_COLOUR, width=2)
        label(right + 6, top - 4, name, LAMP_COLOUR)

    ground_map = config.ground_map()
    if ground_map is not None:
        # Distances are measured inside this outline, which joins the outermost ground points.
        hull = [(float(x), float(y)) for x, y in ground_map.hull]
        draw.line([*hull, hull[0]], fill=SPEED_COLOUR, width=3)
        label(hull[0][0] + 8, hull[0][1] + 8, "metres are measured inside", SPEED_COLOUR)
        for point in config.ground_points:
            x, y = point.pixel
            draw.ellipse([x - 5, y - 5, x + 5, y + 5], outline=SPEED_COLOUR, width=2)
    if config.detectors.speed is not None:
        for name, stretch in config.detectors.speed.stretches.items():
            draw.polygon(stretch.polygon, outline=SPEED_COLOUR, width=2)
            label(
                stretch.polygon[0][0] + 8,
                stretch.polygon[0][1] - 30,
                f"stretch {name}",
                SPEED_COLOUR,
            )

    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="path to site.yaml")
    parser.add_argument("--frame", type=Path, required=True, help="full-resolution frame grab")
    parser.add_argument("--out", type=Path, required=True, help="where to write the preview")
    args = parser.parse_args()

    config, _ = load_site_config(args.config)
    with Image.open(args.frame) as frame:
        render(frame, config).save(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
