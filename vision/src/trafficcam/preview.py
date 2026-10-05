"""Draws the site config's geometry over a frame grab, to check coordinates by eye."""

import argparse
import math
from itertools import cycle
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from trafficcam.config import SiteConfig, load_site_config

GRID_PX = 100
GRID_COLOUR = (255, 255, 255, 70)
CROP_COLOUR = (0, 220, 255)
LINE_COLOUR = (0, 255, 0)
LAMP_COLOUR = (255, 140, 0)
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

    for name, line in config.lines.items():
        (x1, y1), (x2, y2) = line.points
        draw.line([(x1, y1), (x2, y2)], fill=LINE_COLOUR, width=4)
        # Arrowhead at the second point, to show the point order.
        angle = math.atan2(y2 - y1, x2 - x1)
        for side in (-0.5, 0.5):
            tip = (x2 - 24 * math.cos(angle + side), y2 - 24 * math.sin(angle + side))
            draw.line([(x2, y2), tip], fill=LINE_COLOUR, width=4)
        label(x2 + 10, y2 - 12, f"{name} ({line.direction})", LINE_COLOUR)

    for name, head in config.signal_heads.items():
        for _, (x, y, w, h) in head.lamps:
            draw.rectangle([x, y, x + w, y + h], outline=LAMP_COLOUR, width=2)
        x, y, _, _ = head.lamps.red
        label(x + 14, y - 12, name, LAMP_COLOUR)

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
