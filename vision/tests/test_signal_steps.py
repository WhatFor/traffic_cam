"""Finding a signal's changes from the steps in its lamps' scores, whatever the light is doing."""

import math
from datetime import datetime, timedelta

import numpy as np
import pytest
from test_geometry import FPS, START
from test_signal_plan import CONFIG, GREEN, NOT_KNOWN, RED
from test_signals import LIT_IN_SUN, UNLIT_IN_SUN

from trafficcam.contracts import SignalSource, SignalState
from trafficcam.signals import Reading, Signals
from trafficcam.signals.estimator import OFF, ON, StageSequenceEstimator
from trafficcam.signals.steps import StepFinder
from trafficcam.sources import Rgb

HEADS = ["near", "far"]
LAMPS = ("red", "amber", "green")
PALE_VAN: Rgb = (170, 172, 168)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def lit_at(second: float, green_from: float, green_to: float) -> set[str]:
    """Which lamps are lit: red, then red-and-amber for 2 s, green, amber for 3 s, red."""
    if green_from <= second < green_to:
        return {"green"}
    if green_to <= second < green_to + 3:
        return {"amber"}
    return {"red", "amber"} if green_from - 2 <= second < green_from else {"red"}


def sunlit(
    finder: StepFinder,
    seconds: float,
    green: tuple[float, float],
    *,
    start: float = 0,
    van: tuple[float, float] | None = None,
    hidden: str | None = None,
) -> None:
    """Show the finder two heads through a cycle in sun and cloud: faint lamps, and light on
    them that rises and falls by more than a lit lamp adds."""
    rng = np.random.default_rng(1)
    for frame in range(int(start * FPS), int((start + seconds) * FPS)):
        second = frame / FPS
        light = 12 * math.sin(second / 4)
        samples: dict[str, Rgb] = {}
        for head in HEADS:
            lit = set() if head == hidden else lit_at(second, *green)
            for lamp in LAMPS:
                colour = LIT_IN_SUN[lamp] if lamp in lit else UNLIT_IN_SUN
                if van and head == "near" and van[0] <= second < van[1]:
                    colour = PALE_VAN
                r, g, b = (level + light + rng.normal(0, 0.8) for level in colour)
                samples[f"{head}/{lamp}"] = (r, g, b)
        finder.add(at(second), samples)


def test_a_greens_end_is_found_though_the_light_keeps_changing() -> None:
    finder = StepFinder(CONFIG)
    sunlit(finder, 60, green=(10, 31.4))

    found = finder.find(HEADS, OFF, at(15), at(45))

    assert found is not None
    assert found.at == pytest.approx(at(31.4), abs=timedelta(seconds=0.3))


def test_a_greens_start_is_found_by_the_red_and_amber_before_it() -> None:
    finder = StepFinder(CONFIG)
    sunlit(finder, 60, green=(31.4, 55))

    found = finder.find(HEADS, ON, at(15), at(45))

    assert found is not None
    assert found.at == pytest.approx(at(31.4), abs=timedelta(seconds=0.3))


def test_nothing_is_found_where_nothing_changed() -> None:
    finder = StepFinder(CONFIG)
    sunlit(finder, 60, green=(5, 100))

    assert finder.find(HEADS, OFF, at(15), at(45)) is None
    # Nor a start where there was an end.
    assert finder.find(HEADS, ON, at(15), at(45)) is None


def test_a_vehicle_passing_in_front_of_a_head_is_not_a_change() -> None:
    finder = StepFinder(CONFIG)
    sunlit(finder, 60, green=(5, 100), van=(24, 27))
    assert finder.find(HEADS, OFF, at(15), at(45)) is None

    # And with a real end as well, it is the end that is found.
    finder = StepFinder(CONFIG)
    sunlit(finder, 60, green=(5, 36), van=(24, 27))
    found = finder.find(HEADS, OFF, at(15), at(45))
    assert found is not None
    assert found.at == pytest.approx(at(36), abs=timedelta(seconds=0.3))


def test_one_clear_head_is_enough_for_a_group_of_two() -> None:
    # In sunshine one head of a pair is often washed out altogether.
    finder = StepFinder(CONFIG)
    sunlit(finder, 60, green=(10, 31.4), hidden="far")

    found = finder.find(HEADS, OFF, at(15), at(45))

    assert found is not None
    assert found.at == pytest.approx(at(31.4), abs=timedelta(seconds=0.3))


def test_a_change_not_yet_seen_in_full_is_not_looked_for() -> None:
    finder = StepFinder(CONFIG)
    sunlit(finder, 32, green=(10, 31.4))

    assert finder.find(HEADS, OFF, at(15), at(45)) is None


def test_a_loosely_placed_change_is_looked_for_and_then_known() -> None:
    """Neither head of `main` can be read. The side head places main's green start, at 120,
    but not its end, 10 to 38 s later. The lamps' steps do: it ended at 145."""

    def line_states(with_steps: bool) -> list[tuple[SignalState, SignalSource | None]]:
        steps = StepFinder(CONFIG) if with_steps else None
        signals = Signals(CONFIG, "sha256:test", StageSequenceEstimator(CONFIG), steps)
        finder = StepFinder(CONFIG)
        frames: list[tuple[datetime, dict[str, Rgb]]] = []
        finder.add = lambda timestamp, samples: frames.append((timestamp, dict(samples)))  # type: ignore[method-assign]
        sunlit(finder, 80, green=(120, 145), start=90)
        side_since = (RED, at(90))
        for timestamp, samples in frames:
            second = (timestamp - START).total_seconds()
            side = GREEN if 100 <= second < 110 else RED
            if side != side_since[0]:
                side_since = (side, timestamp)
            readings = {
                "near": Reading(NOT_KNOWN, at(90)),
                "far": Reading(NOT_KNOWN, at(90)),
                "side": Reading(*side_since),
            }
            signals.update(timestamp, readings, samples)
        return [
            (line.state, line.source)
            for moment in (125, 140, 146.5, 152)
            for line in [signals.line_state("stopline", at(moment))]
            if line is not None
        ]

    inferred = SignalSource.inferred
    assert line_states(with_steps=False) == [
        (GREEN, inferred),
        (NOT_KNOWN, None),
        (NOT_KNOWN, None),
        (NOT_KNOWN, None),
    ]
    assert line_states(with_steps=True) == [
        (GREEN, inferred),
        (GREEN, inferred),
        (SignalState.amber, inferred),
        (RED, inferred),
    ]
