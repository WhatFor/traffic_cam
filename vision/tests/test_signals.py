"""Signals: reading heads from lamp samples, and the history kept of their states."""

import copy
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import numpy as np
import pytest
from test_geometry import FPS, SITE, at

from trafficcam.config import SiteConfig
from trafficcam.contracts import SignalSource, SignalState
from trafficcam.signals import Reading, Signals
from trafficcam.signals.lamps import LampLevels, LampRoiObserver, lamp_score, state_for
from trafficcam.sources import Frame, Rgb

RED, RED_AMBER, GREEN, AMBER, UNKNOWN = (
    SignalState.red,
    SignalState.red_amber,
    SignalState.green,
    SignalState.amber,
    SignalState.unknown,
)

# Colours measured on real heads at dusk (docs/spikes/lamp-readability.md).
LIT: dict[str, Rgb] = {"red": (170, 19, 41), "amber": (163, 88, 34), "green": (100, 193, 159)}
UNLIT: Rgb = (30, 30, 34)
NO_IMAGE = np.empty((0, 0, 3), dtype=np.uint8)

# The geometry test site with four heads: two on the stop line, one whose red cannot be
# seen, and a pedestrian signal.
SIGNAL_SITE: dict[str, Any] = copy.deepcopy(SITE)
SIGNAL_SITE["signal_heads"] = {
    "near": {
        "lamps": {"red": [10, 10, 5, 5], "amber": [10, 20, 5, 5], "green": [10, 30, 5, 5]},
        "controls": ["stopline"],
    },
    "far": {
        "lamps": {"red": [50, 10, 5, 5], "amber": [50, 20, 5, 5], "green": [50, 30, 5, 5]},
        "controls": ["stopline"],
    },
    "side": {"lamps": {"green": [90, 30, 3, 3]}, "controls": []},
    "crossing": {"lamps": {"red": [120, 10, 3, 3], "green": [120, 20, 3, 3]}, "controls": []},
}
CONFIG = SiteConfig.model_validate(SIGNAL_SITE)

CYCLE = [(GREEN, 6.0), (AMBER, 3.0), (RED, 8.0), (RED_AMBER, 2.0)]
LAMPS_LIT = {GREEN: {"green"}, AMBER: {"amber"}, RED: {"red"}, RED_AMBER: {"red", "amber"}}


def samples_for(head: str, lit: set[str]) -> dict[str, Rgb]:
    return {
        f"{head}/{colour}": LIT[colour] if colour in lit else UNLIT
        for colour in CONFIG.signal_heads[head].lamps
    }


def cycles(count: int) -> list[set[str]]:
    """Which lamps of a three-lamp head are lit in each frame of `count` signal cycles."""
    return [
        LAMPS_LIT[state]
        for _ in range(count)
        for state, seconds in CYCLE
        for _ in range(int(seconds * FPS))
    ]


def watch(head: str, lit_by_frame: list[set[str]]) -> list[Reading]:
    """One head's readings as its lamps follow `lit_by_frame`; the other heads stay dark."""
    observer = LampRoiObserver(CONFIG)
    dark = {
        key: colour
        for other in CONFIG.signal_heads
        for key, colour in samples_for(other, set()).items()
    }
    return [
        observer.read(Frame(index, at(index), NO_IMAGE, dark | samples_for(head, lit)))[head]
        for index, lit in enumerate(lit_by_frame)
    ]


def runs(readings: list[Reading]) -> Iterator[tuple[SignalState, int]]:
    start = 0
    for index in range(1, len(readings) + 1):
        if index == len(readings) or readings[index].state != readings[start].state:
            yield readings[start].state, index - start
            start = index


def test_a_red_glow_is_not_amber_and_a_pale_vehicle_is_not_red() -> None:
    red_glow, pale = (150, 20, 40), (200, 200, 200)

    assert lamp_score(red_glow, "amber") < 10 < lamp_score(LIT["amber"], "amber")
    assert lamp_score(pale, "red") < 10 < lamp_score(LIT["red"], "red")


def test_levels_wait_until_a_lamp_has_been_seen_lit_and_unlit() -> None:
    levels = LampLevels(FPS)
    noise = np.random.default_rng(0).normal(5, 6, size=20 * FPS)

    for score in noise:
        levels.add(float(score))
    assert not levels.established

    for score in [120.0] * (3 * FPS) + [5.0] * FPS:
        levels.add(score)
    assert levels.established
    assert levels.margin(120.0) > 0 > levels.margin(5.0)


@pytest.mark.parametrize(
    ("lit", "lamps", "state"),
    [
        ({"red"}, {"red", "amber", "green"}, RED),
        ({"red", "amber"}, {"red", "amber", "green"}, RED_AMBER),
        ({"green"}, {"red", "amber", "green"}, GREEN),
        ({"amber"}, {"red", "amber", "green"}, AMBER),
        (set(), {"red", "amber", "green"}, UNKNOWN),
        ({"red", "green"}, {"red", "amber", "green"}, UNKNOWN),
        (set(), {"green"}, RED),
        ({"green"}, {"green"}, GREEN),
        (set(), {"red", "green"}, UNKNOWN),
    ],
)
def test_lit_lamps_mean_a_state(lit: set[str], lamps: set[str], state: SignalState) -> None:
    assert state_for(frozenset(lit), frozenset(lamps)) == state


def test_a_head_is_unknown_until_every_lamp_has_shown_itself_then_follows_the_cycle() -> None:
    readings = watch("near", cycles(3))

    seen = [state for state, _ in runs(readings)]
    assert seen[0] == UNKNOWN
    # Red is the last lamp to light in the first cycle; from then on every state is read.
    assert seen[1:] == [RED, RED_AMBER, GREEN, AMBER, RED, RED_AMBER, GREEN, AMBER, RED, RED_AMBER]
    lengths = dict(list(runs(readings))[-4:-1])
    assert lengths[GREEN] == pytest.approx(6 * FPS, abs=1)
    assert lengths[AMBER] == pytest.approx(3 * FPS, abs=1)


def test_one_dark_frame_of_a_lit_lamp_changes_nothing() -> None:
    lit = cycles(3)
    flicker_at = len(lit) - 5 * FPS  # well inside the last red
    lit[flicker_at] = set()

    readings = watch("near", lit)

    assert {reading.state for reading in readings[flicker_at - 5 : flicker_at + 10]} == {RED}


def test_a_meaningless_combination_must_last_before_the_head_goes_unknown() -> None:
    lit = cycles(3)
    brief, lasting = len(lit) - 7 * FPS, len(lit) - 4 * FPS
    lit[brief : brief + FPS // 2] = [{"red", "amber", "green"}] * (FPS // 2)
    lit[lasting : lasting + 2 * FPS] = [{"red", "amber", "green"}] * (2 * FPS)

    readings = watch("near", lit)

    assert {reading.state for reading in readings[brief : brief + FPS]} == {RED}
    assert readings[lasting + 2 * FPS - 1].state == UNKNOWN
    # It is dated from when the combination first showed, not from when it was believed.
    assert readings[lasting + 2 * FPS - 1].since == pytest.approx(
        at(lasting), abs=timedelta(seconds=0.3)
    )
    # The lamps go back to the cycle, which ends on red and amber.
    assert readings[-1].state == RED_AMBER


def test_a_change_out_of_sequence_is_believed_only_if_it_lasts() -> None:
    lit = cycles(3)
    start = len(lit) - 7 * FPS  # red; green is not what follows red
    lit[start : start + FPS // 2] = [{"green"}] * (FPS // 2)
    brief = watch("near", lit)
    lit[start : start + 3 * FPS] = [{"green"}] * (3 * FPS)
    lasting = watch("near", lit)

    assert {reading.state for reading in brief[start : start + FPS]} == {RED}
    assert lasting[start + 2 * FPS].state == GREEN


def test_a_head_without_a_red_lamp_is_red_when_not_green() -> None:
    lit = ([set()] * (8 * FPS) + [{"green"}] * (4 * FPS)) * 3

    readings = watch("side", lit)

    assert [state for state, _ in runs(readings)][-4:] == [RED, GREEN, RED, GREEN]


def test_a_brief_flash_on_a_head_with_no_sequence_is_ignored() -> None:
    lit = ([set()] * (8 * FPS) + [{"green"}] * (4 * FPS)) * 3 + [set()] * (8 * FPS)
    flash = len(lit) - 4 * FPS
    lit[flash : flash + FPS] = [{"green"}] * FPS

    readings = watch("side", lit)

    assert {reading.state for reading in readings[flash : flash + 2 * FPS]} == {RED}


def test_frames_without_samples_read_nothing() -> None:
    assert LampRoiObserver(CONFIG).read(Frame(0, at(0), NO_IMAGE)) == {}


def history(*changes: tuple[str, SignalState, int]) -> Signals:
    """A history in which each head changed to a state at a frame."""
    signals = Signals(CONFIG, "sha256:test")
    for head, state, frame in changes:
        signals.update(at(frame), {head: Reading(state, at(frame))})
    return signals


def test_a_change_of_state_is_reported_once_as_a_contract_record() -> None:
    signals = Signals(CONFIG, "sha256:test")

    first = signals.update(at(0), {"near": Reading(RED, at(0), 0.9)})
    same = signals.update(at(1), {"near": Reading(RED, at(0), 0.9)})
    (second,) = signals.update(at(30), {"near": Reading(RED_AMBER, at(28), 0.8)})

    assert (first[0].from_state, first[0].to_state, first[0].ts) == (None, RED, at(0))
    assert same == []
    assert (second.from_state, second.to_state, second.ts) == (RED, RED_AMBER, at(28))
    assert (second.head_id, second.source, second.confidence) == (
        "near",
        SignalSource.observed,
        0.8,
    )
    assert (
        second.id
        == history(("near", RED, 0)).update(at(30), {"near": Reading(RED_AMBER, at(28))})[0].id
    )


def test_a_red_that_cannot_be_seen_is_marked_inferred() -> None:
    signals = Signals(CONFIG, "sha256:test")

    (red,) = signals.update(at(0), {"side": Reading(RED, at(0))})
    (green,) = signals.update(at(10), {"side": Reading(GREEN, at(10))})

    assert (red.source, green.source) == (SignalSource.inferred, SignalSource.observed)


def test_the_state_at_a_past_moment_can_be_asked_for() -> None:
    signals = history(("near", RED, 0), ("near", RED_AMBER, 30), ("near", GREEN, 60))

    assert signals.state_of("near", at(45)) == (RED_AMBER, at(30))
    assert signals.state_of("near", at(60)) == (GREEN, at(60))
    assert signals.current()["near"] == GREEN
    assert signals.current()["far"] == UNKNOWN


def test_a_line_takes_the_state_its_heads_agree_on() -> None:
    signals = history(("near", RED, 0), ("far", RED, 2), ("near", GREEN, 60))

    agreed = signals.line_state("stopline", at(30))
    assert agreed is not None
    assert (agreed.state, agreed.source, agreed.since) == (RED, SignalSource.observed, at(2))
    # near has gone green and far has not: they disagree.
    disagreeing = signals.line_state("stopline", at(61))
    assert disagreeing is not None
    assert disagreeing.state == UNKNOWN


def test_an_unknown_head_is_left_out_of_the_agreement() -> None:
    signals = history(("near", RED, 0), ("far", UNKNOWN, 0))

    state = signals.line_state("stopline", at(10))

    assert state is not None
    assert state.state == RED


def test_a_line_no_head_controls_has_no_state() -> None:
    assert history(("near", RED, 0)).line_state("another_line", at(10)) is None


def test_time_spent_in_a_state_is_counted_between_two_moments() -> None:
    signals = history(("side", RED, 0), ("side", GREEN, 30), ("side", RED, 90))

    def green_between(start: int, end: int) -> float:
        return signals.seconds_in("side", GREEN, at(start), at(end))

    assert green_between(0, 150) == pytest.approx(60 / FPS)
    assert green_between(60, 150) == pytest.approx(30 / FPS)
    assert green_between(0, 20) == 0


def test_readings_for_heads_the_config_does_not_have_are_ignored() -> None:
    signals = Signals(CONFIG, "sha256:test")

    assert signals.update(at(0), {"removed_head": Reading(RED, at(0))}) == []
