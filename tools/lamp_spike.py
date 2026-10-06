"""Spike: can signal lamps be read from the camera's main stream?

Run from vision/ so its environment is used:

    uv run python ../tools/lamp_spike.py find CLIP HEADS.json LAMPS.json
    uv run python ../tools/lamp_spike.py measure CLIP LAMPS.json OUT_DIR

`find` takes rough head centres ({"A": [x, y], ...}) and locates each head's red, amber
and green lamps from which pixels change in which colour over the clip. `measure` reads
the lamps in every frame and reports whether the signal states can be told apart.

LAMPS.json maps a head's name to its lamps' centres in frame pixels, for example
{"A": {"red": [594, 748], "amber": [595, 757], "green": [594, 768], "size": 5}}. A head
may have fewer lamps (a pedestrian signal has red and green); "size" is optional.
"""

import argparse
import json
from collections import Counter
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw

COLOURS = ("red", "amber", "green")
SEARCH = (24, 52)  # width and height searched around a head's centre
VOTE_FRAMES = 5
# UK sequence: each state and the one that may follow it.
NEXT = {"red": "red+amber", "red+amber": "green", "green": "amber", "amber": "red"}


def frames(clip: Path) -> Iterator[tuple[float, np.ndarray]]:
    with av.open(str(clip)) as container:
        for frame in container.decode(video=0):
            yield float(frame.time or 0), frame.to_ndarray(format="rgb24")


def lamp_score(rgb: np.ndarray, colour: str) -> np.ndarray:
    """How lit a lamp looks, from its mean colour.

    A lit red is dim but strongly red, so brightness alone confuses it with a pale vehicle
    passing behind the head. Amber needs green as well as red, or the glow of the red lamp
    just above it counts. Green washes out towards white, so it is scored mostly on brightness.
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    if colour == "red":
        return r - np.maximum(g, b)
    if colour == "amber":
        return np.minimum(r, g) - b / 2
    return g - r / 2


def find(clip: Path, heads_path: Path, lamps_path: Path) -> None:
    heads = json.loads(heads_path.read_text())
    width, height = SEARCH
    stacks: dict[str, list[np.ndarray]] = {name: [] for name in heads}
    for _, image in frames(clip):
        for name, (x, y) in heads.items():
            stacks[name].append(
                image[
                    y - height // 2 : y + height // 2, x - width // 2 : x + width // 2
                ]
            )
    lamps = {}
    for name, (x, y) in heads.items():
        stack = np.stack(stacks[name]).astype(np.float32)
        # Percentiles, not min and max, so one passing headlight does not count as a lamp.
        swing = np.percentile(stack, 98, axis=0) - np.percentile(stack, 2, axis=0)
        r, g, b = swing[..., 0], swing[..., 1], swing[..., 2]
        scores = {
            "red": r - np.maximum(g, b),
            "amber": np.minimum(r, g) - b,
            "green": np.minimum(g, b) - r,
        }
        lamps[name] = {}
        for colour, score in scores.items():
            rows, columns = np.unravel_index(
                np.argsort(score.ravel())[-12:], score.shape
            )
            lamps[name][colour] = [
                round(x - width // 2 + float(columns.mean()), 1),
                round(y - height // 2 + float(rows.mean()), 1),
            ]
        print(name, lamps[name])
    lamps_path.write_text(json.dumps(lamps, indent=2) + "\n")


def states_of(lit: np.ndarray, colours: list[str]) -> list[str]:
    names = []
    for row in lit:
        on = [colour for colour, is_on in zip(colours, row, strict=True) if is_on]
        names.append("+".join(on) if on else "dark")
    return names


def vote(names: list[str]) -> list[str]:
    half = VOTE_FRAMES // 2
    return [
        Counter(names[max(0, i - half) : i + half + 1]).most_common(1)[0][0]
        for i in range(len(names))
    ]


def runs(names: list[str], times: np.ndarray) -> list[tuple[str, float, float]]:
    out, start = [], 0
    for i in range(1, len(names) + 1):
        if i == len(names) or names[i] != names[start]:
            out.append(
                (names[start], float(times[start]), float(times[i - 1] - times[start]))
            )
            start = i
    return out


def sample(image: np.ndarray, x: float, y: float, size: int) -> np.ndarray:
    half = size // 2
    patch = image[
        round(y) - half : round(y) + half + 1, round(x) - half : round(x) + half + 1
    ]
    return patch.reshape(-1, 3).mean(axis=0)


def measure(clip: Path, lamps_path: Path, out: Path, default_size: int) -> None:
    lamps = json.loads(lamps_path.read_text())
    present = {name: [c for c in COLOURS if c in head] for name, head in lamps.items()}
    sizes = {name: head.get("size", default_size) for name, head in lamps.items()}
    times: list[float] = []
    samples: dict[str, list[list[np.ndarray]]] = {name: [] for name in lamps}
    for time, image in frames(clip):
        times.append(time)
        for name, head in lamps.items():
            samples[name].append(
                [sample(image, *head[colour], sizes[name]) for colour in present[name]]
            )
    seconds = np.array(times)
    out.mkdir(parents=True, exist_ok=True)
    strip_height, label_width, gap = 12, 30, 8
    tops = np.cumsum([0] + [len(present[name]) * strip_height + gap for name in lamps])
    sheet = Image.new("RGB", (label_width + len(seconds) // 2, int(tops[-1])), "black")
    draw = ImageDraw.Draw(sheet)
    report = [
        f"{clip.name}: {len(seconds)} frames over {seconds[-1] - seconds[0]:.0f} s\n"
    ]

    for top, (name, head) in zip(tops, lamps.items(), strict=False):
        colours = present[name]
        rgb = np.array(samples[name], dtype=np.float32)  # frames x lamps x 3
        y = np.stack(
            [lamp_score(rgb[:, index], colour) for index, colour in enumerate(colours)],
            axis=1,
        )
        # Wide percentiles, because amber and a pedestrian green are lit for only a few
        # seconds of each cycle.
        low, high = np.percentile(y, 2, axis=0), np.percentile(y, 99, axis=0)
        lit = y > (low + high) / 2
        voted = vote(states_of(lit, colours))
        spacing = " and ".join(
            f"{np.hypot(*(np.array(head[a]) - np.array(head[b]))):.1f}"
            for a, b in pairwise(colours)
        )
        report.append(
            f"head {name}: lamps {spacing} px apart, sampled {sizes[name]}x{sizes[name]} px"
        )
        for index, colour in enumerate(colours):
            on, off = y[lit[:, index], index], y[~lit[:, index], index]
            if len(on) < 10 or len(off) < 10:
                report.append(
                    f"  {colour:5}: never changes (score {low[index]:.0f} to {high[index]:.0f})"
                )
                continue
            noise = float(np.sqrt((on.std() ** 2 + off.std() ** 2) / 2))
            # A frame that reads dark although the voted state says the lamp is on.
            should_be_on = np.array([colour in state.split("+") for state in voted])
            dropouts = int((should_be_on & ~lit[:, index]).sum())
            lit_colour = tuple(
                int(v) for v in rgb[lit[:, index], index].mean(axis=0).round()
            )
            report.append(
                f"  {colour:5}: lit {on.mean():5.0f} / unlit {off.mean():5.0f} score,"
                f" separation {(on.mean() - off.mean()) / max(noise, 1e-6):4.1f} sd,"
                f" lit colour rgb{lit_colour},"
                f" {dropouts} dark frames of {int(should_be_on.sum())} lit"
            )
        sequence = [run for run in runs(voted, seconds) if run[2] >= 0.3]
        report.append(
            "  sequence: "
            + ", ".join(f"{state} {duration:.1f}s" for state, _, duration in sequence)
        )
        if colours == list(COLOURS):
            wrong = sum(
                1 for (a, _, _), (b, _, _) in pairwise(sequence) if NEXT.get(a) != b
            )
            report.append(
                f"  changes out of the UK order: {wrong} of {max(len(sequence) - 1, 0)}"
            )
        both = sum(
            duration
            for state, _, duration in sequence
            if "red" in state and "green" in state
        )
        report.append(f"  time read as red and green together: {both:.1f} s\n")

        draw.text((4, int(top) + strip_height // 2), name, fill="white")
        # Two frames to a pixel.
        strip = rgb[: len(rgb) // 2 * 2].reshape(-1, 2, len(colours), 3).mean(axis=1)
        for index in range(len(colours)):
            band = np.repeat(strip[:, index][None, :, :], strip_height, axis=0).astype(
                np.uint8
            )
            sheet.paste(
                Image.fromarray(band), (label_width, int(top) + index * strip_height)
            )

    sheet.save(out / f"{clip.stem}_timeline.png")
    text = "\n".join(report)
    (out / f"{clip.stem}_report.txt").write_text(text + "\n")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    finder = commands.add_parser("find")
    finder.add_argument("clip", type=Path)
    finder.add_argument("heads", type=Path)
    finder.add_argument("lamps", type=Path)
    measurer = commands.add_parser("measure")
    measurer.add_argument("clip", type=Path)
    measurer.add_argument("lamps", type=Path)
    measurer.add_argument("out", type=Path)
    measurer.add_argument(
        "--size", type=int, default=5, help="side of the square sampled"
    )
    args = parser.parse_args()
    if args.command == "find":
        find(args.clip, args.heads, args.lamps)
    else:
        measure(args.clip, args.lamps, args.out, args.size)


if __name__ == "__main__":
    main()
