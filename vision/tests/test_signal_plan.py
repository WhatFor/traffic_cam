"""The plan of the signals, and the states worked out from it for heads that cannot be read."""

import copy
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError
from test_geometry import START
from test_signals import SIGNAL_SITE

from trafficcam.config import SiteConfig
from trafficcam.contracts import SignalSource, SignalState
from trafficcam.signals import UNKNOWN, LineState, Reading, Signals
from trafficcam.signals.estimator import OFF, ON, Placed, PlanTimings, StageSequenceEstimator
from trafficcam.signals.study import gaps, usual

RED, RED_AMBER, GREEN, AMBER, NOT_KNOWN = (
    SignalState.red,
    SignalState.red_amber,
    SignalState.green,
    SignalState.amber,
    SignalState.unknown,
)

# A cycle of about a minute. `main` (the heads `near` and `far`, on `stopline`) is green for
# 10 to 38 s; 5 s after it ends `side` is green for 10 s; 10 s after that `main` is green again.
# Between the two, 2 s after `side` ends, a head nobody can see is green for 3 s.
PLAN_SITE: dict[str, Any] = copy.deepcopy(SIGNAL_SITE)
PLAN_SITE["lines"]["stopline_unseen"] = {
    "points": [[900, 300], [1100, 300]],
    "direction": "inbound",
}
PLAN_SITE["signal_plan"] = {
    "groups": {
        "main": {"heads": ["near", "far"]},
        "side": {"heads": ["side"]},
        "crossing": {"heads": ["crossing"]},
        "unseen": {"controls": ["stopline_unseen"]},
    },
    "links": [
        {"from": "main.on", "to": "main.off", "s": [10, 38]},
        {"from": "main.off", "to": "side.on", "s": [4.9, 5.1]},
        {"from": "side.on", "to": "side.off", "s": [9.9, 10.1]},
        {"from": "side.off", "to": "main.on", "s": [9.9, 10.1]},
        {"from": "side.off", "to": "unseen.on", "s": [1.9, 2.1]},
        {"from": "unseen.on", "to": "unseen.off", "s": [2.9, 3.1]},
        {"from": "unseen.off", "to": "main.on", "s": [4.9, 5.1]},
    ],
}
CONFIG = SiteConfig.model_validate(PLAN_SITE)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def side_green(estimator: StageSequenceEstimator, start: float) -> None:
    """The side head is seen to go green at `start` and red 10 s later."""
    estimator.observe("side", RED, GREEN, at(start))
    estimator.observe("side", GREEN, RED, at(start + 10))


def test_gaps_add_up_along_the_links_in_either_direction() -> None:
    timings = PlanTimings(CONFIG.signal_plan)  # type: ignore[arg-type]

    # Main's green starts 20 s after the side's does, and the one before it ended 5 s before.
    earlier, later = sorted(timings.reach[(("side", ON), ("main", ON))])
    assert later == pytest.approx((19.8, 20.2))
    before, after = sorted(timings.reach[(("side", ON), ("main", OFF))])
    assert before == pytest.approx((-5.1, -4.9))
    # The green before that began 10 to 38 s before it ended, and the one after ends 10 to
    # 38 s after it starts.
    assert earlier == pytest.approx((-43.1, -14.9))
    assert after == pytest.approx((29.8, 58.2))
    # From the end of main's green to its next start, by the soonest way round.
    assert timings.following[(("main", OFF), ("main", ON))] == pytest.approx((24.5, 25.5))
    assert timings.following[(("main", ON), ("main", OFF))] == (10, 38)


def test_one_head_places_the_changes_of_another_before_and_after_it() -> None:
    estimator = StageSequenceEstimator(CONFIG)
    side_green(estimator, 100)

    def main(seconds: float) -> SignalState:
        return estimator.state_of_group("main", at(seconds))[0]

    # Main's green ended 5 s before the side's began, and starts again 20 s after.
    assert [main(s) for s in (96, 99, 115, 119, 121, 129)] == [
        AMBER,
        RED,
        RED,
        RED_AMBER,
        GREEN,
        GREEN,
    ]
    # Since the latest it can have begun, 10.1 s after the side's green was seen to end; and
    # that start is placed to within 0.2 s.
    placed = estimator.state_of("stopline", at(121))
    assert placed is not None
    assert (placed.state, placed.since) == (GREEN, at(120.1))
    assert placed.within_s == pytest.approx(0.2)


UNKNOWN_STATE = Placed(NOT_KNOWN)


def test_a_state_is_given_only_where_the_changes_leave_no_doubt() -> None:
    estimator = StageSequenceEstimator(CONFIG)
    side_green(estimator, 100)

    def main(seconds: float) -> SignalState:
        return estimator.state_of_group("main", at(seconds))[0]

    # The 0.2 s either side of a change that could be either state.
    assert main(94.95) == NOT_KNOWN
    assert main(120.0) == NOT_KNOWN
    # Main's green lasts 10 to 38 s. Past 10 s nothing says whether it has ended yet.
    assert main(135) == NOT_KNOWN

    # The side head going green again places that end, 5 s before: it was at 145.
    side_green(estimator, 150)
    assert [main(s) for s in (135, 144, 146, 149)] == [GREEN, GREEN, AMBER, RED]


def test_a_head_nobody_can_see_is_placed_too() -> None:
    estimator = StageSequenceEstimator(CONFIG)
    side_green(estimator, 50)
    side_green(estimator, 100)

    def unseen(seconds: float) -> SignalState:
        return (estimator.state_of("stopline_unseen", at(seconds)) or UNKNOWN_STATE).state

    # Green from 112 to 115, with red-and-amber before it and amber after.
    assert [unseen(s) for s in (111, 113, 116, 119)] == [RED_AMBER, GREEN, AMBER, RED]
    assert estimator.state_of("no_such_line", at(113)) is None


def test_with_nothing_seen_nothing_is_known() -> None:
    estimator = StageSequenceEstimator(CONFIG)

    assert estimator.state_of_group("main", at(100)) == UNKNOWN_STATE


def test_a_head_coming_back_into_view_is_not_a_change() -> None:
    estimator = StageSequenceEstimator(CONFIG)
    estimator.observe("side", NOT_KNOWN, GREEN, at(100))
    estimator.observe("side", GREEN, NOT_KNOWN, at(104))
    estimator.observe("near", RED, GREEN, at(120))  # not a step of a three-lamp head's sequence

    assert estimator.state_of_group("main", at(121)) == UNKNOWN_STATE
    assert estimator.placed("side", ON, at(100)) == []


def test_heads_of_one_group_changing_together_are_one_change() -> None:
    estimator = StageSequenceEstimator(CONFIG)
    estimator.observe("near", RED_AMBER, GREEN, at(120.0))
    estimator.observe("far", RED_AMBER, GREEN, at(120.2))

    (window,) = estimator.placed("main", ON, at(120))
    assert (window.earliest, window.latest) == (at(120.0).timestamp(), at(120.2).timestamp())


def test_something_passing_in_front_of_a_green_head_is_not_its_end() -> None:
    estimator = StageSequenceEstimator(CONFIG)
    estimator.observe("near", RED_AMBER, GREEN, at(120))
    estimator.observe("near", GREEN, AMBER, at(125))
    assert at(125).timestamp() in [w.latest for w in estimator.placed("main", OFF, at(125))]

    estimator.observe("near", AMBER, GREEN, at(126))

    assert at(125).timestamp() not in [w.latest for w in estimator.placed("main", OFF, at(125))]


def test_a_change_outside_a_fixed_gap_is_counted() -> None:
    estimator = StageSequenceEstimator(CONFIG)
    side_green(estimator, 100)
    assert estimator.violations == 0

    # Main should go green 10 s after the side's green ended. It is seen to do so at 13 s.
    estimator.observe("near", RED_AMBER, GREEN, at(123))
    assert estimator.violations == 1
    # What was seen is believed over what was worked out.
    assert estimator.state_of_group("main", at(121))[0] != GREEN
    assert estimator.state_of_group("main", at(124))[0] == GREEN


def showing(signals: Signals, seconds: float, **heads: SignalState) -> None:
    signals.update(
        at(seconds), {head: Reading(state, at(seconds)) for head, state in heads.items()}
    )


def test_a_line_whose_heads_cannot_be_read_takes_the_inferred_state() -> None:
    signals = Signals(CONFIG, "sha256:test", StageSequenceEstimator(CONFIG))
    showing(signals, 90, near=NOT_KNOWN, far=NOT_KNOWN, side=RED)
    showing(signals, 100, side=GREEN)
    showing(signals, 110, side=RED)

    line = signals.line_state("stopline", at(121))
    assert line is not None
    assert (line.state, line.source, line.since) == (GREEN, SignalSource.inferred, at(120.1))
    assert line.placed_within_s == pytest.approx(0.2)
    unseen = signals.line_state("stopline_unseen", at(113))
    assert unseen is not None
    assert (unseen.state, unseen.source, unseen.since) == (GREEN, SignalSource.inferred, at(112.1))
    # Where the changes leave doubt the line is unknown, as before.
    assert signals.line_state("stopline", at(135)) == UNKNOWN


def test_a_head_that_can_be_read_is_never_overruled() -> None:
    signals = Signals(CONFIG, "sha256:test", StageSequenceEstimator(CONFIG))
    showing(signals, 90, near=RED, far=NOT_KNOWN, side=RED)
    showing(signals, 100, side=GREEN)
    showing(signals, 110, side=RED)

    # The plan says green at 121. The one head that can be read says red.
    assert signals.line_state("stopline", at(121)) == LineState(RED, SignalSource.observed, at(90))


def main_shows(second: int, misreading: bool) -> SignalState:
    """What a head of `main` shows through a 60 s cycle in which the side head goes green
    at 100, 160 and so on. A misreading one shows red-and-amber for 15 s of every red."""
    into = (second - 100) % 60
    if 18 <= into < 20 or (misreading and into < 15):
        return RED_AMBER
    return GREEN if 20 <= into < 55 else AMBER if 55 <= into < 58 else RED


def watched(seconds: int, misreading: bool) -> Signals:
    """Signals after some minutes of the side head and one head of `main`, read each second."""
    signals = Signals(CONFIG, "sha256:test", StageSequenceEstimator(CONFIG))
    since: dict[str, tuple[SignalState, int]] = {}
    for second in range(90, 90 + seconds):
        showing = {
            "near": main_shows(second, misreading),
            "side": GREEN if (second - 100) % 60 < 10 else RED,
        }
        for head, state in showing.items():
            if head not in since or since[head][0] != state:
                since[head] = (state, second)
        readings = {head: Reading(state, at(began)) for head, (state, began) in since.items()}
        signals.update(at(second), readings)
    return signals


def test_a_head_that_keeps_contradicting_the_plan_is_overruled_where_it_does() -> None:
    signals = watched(300, misreading=True)

    assert signals.doubted == {"near"}
    # 345 is 5 s into one of its false red-and-ambers; the plan says red, to within 0.2 s.
    line = signals.line_state("stopline", at(345))
    assert line is not None
    assert (line.state, line.source) == (RED, SignalSource.inferred)
    # Where the plan does not rule out what it shows, it is still what the line goes by.
    green = signals.line_state("stopline", at(370))
    assert green is not None
    assert (green.state, green.source) == (GREEN, SignalSource.observed)


def test_a_head_that_agrees_with_the_plan_is_believed() -> None:
    signals = watched(300, misreading=False)

    assert signals.doubted == frozenset()
    red = signals.line_state("stopline", at(345))
    assert red is not None
    assert (red.state, red.source) == (RED, SignalSource.observed)


def test_without_an_estimator_an_unread_line_is_unknown() -> None:
    signals = Signals(CONFIG, "sha256:test")
    showing(signals, 90, near=NOT_KNOWN, far=NOT_KNOWN)

    assert signals.line_state("stopline", at(121)) == UNKNOWN
    assert signals.line_state("stopline_unseen", at(113)) is None


def plan_with(change: dict[str, Any]) -> dict[str, Any]:
    site = copy.deepcopy(PLAN_SITE)
    site["signal_plan"] |= change
    return site


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"groups": {"main": {"heads": ["nowhere"]}}}, "unknown head 'nowhere'"),
        ({"groups": {"a": {"heads": ["near"]}, "b": {"heads": ["near"]}}}, "another group"),
        ({"groups": {"unseen": {"controls": ["no_line"]}}}, "unknown 'no_line'"),
        ({"links": [{"from": "main.on", "to": "elsewhere.off", "s": [1, 2]}]}, "elsewhere.off"),
        ({"links": [{"from": "main.started", "to": "main.off", "s": [1, 2]}]}, "main.started"),
        ({"links": [{"from": "main.on", "to": "main.off", "s": [3, 2]}]}, "least is more"),
    ],
)
def test_a_plan_that_names_what_is_not_there_is_refused(
    change: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        SiteConfig.model_validate(plan_with(change))


def test_a_gap_is_taken_to_the_next_change_of_the_same_cycle() -> None:
    assert gaps([0.0, 100.0, 200.0], [4.0, 104.5, 500.0]).tolist() == [4.0, 4.5]


def test_the_usual_gap_leaves_out_the_strays() -> None:
    # Fixed at 4 s, read to a tenth of a second, with two pairings across cycles.
    seen = np.array([4.0] * 60 + [3.9] * 20 + [4.1] * 20 + [88.0, 92.0])
    span, inside = usual(seen)

    assert span == (3.6, 4.4)
    assert inside == pytest.approx(100 / 102)

    # One that varies with traffic is given room beyond what was seen.
    varying, _ = usual(np.linspace(20.0, 30.0, 101))
    assert varying[0] < 20.0 < 30.0 < varying[1]
