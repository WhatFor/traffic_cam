"""Learn the plan of the signals from what has been recorded, and test the estimator on it.

    python -m trafficcam.signals.study learn --config ../config/site.yaml --changes changes.csv
    python -m trafficcam.signals.study flow ... --crossings crossings.csv
    python -m trafficcam.signals.study evaluate ... --crossings crossings.csv

`changes.csv` holds head, seconds since 1970, state; `crossings.csv` holds line, seconds.
`just signal-history` writes both from what ingest has stored.
"""

import argparse
import bisect
import csv
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from trafficcam.config import ConfigError, SignalLink, SignalPlan, SiteConfig, load_site_config
from trafficcam.contracts import SignalState
from trafficcam.signals.estimator import (
    FIXED_WIDTH_S,
    NEAR_S,
    OFF,
    ON,
    SAME_CHANGE_S,
    UNKNOWN,
    Change,
    PlanTimings,
    Span,
    StageSequenceEstimator,
    edge_of,
)

NOT_KNOWN = SignalState.unknown

HeadChange = tuple[float, str, SignalState | None, SignalState]  # when, head, was, now

# A gap seen fewer times than this is not offered as a link.
MIN_SEEN = 30
# Between two changes of one cycle: further apart than this and they belong to different ones.
SAME_CYCLE_S = 150.0
# Added either side of what was seen, for what was not.
MARGIN_S = 0.3
# A gap that varies is given this share of its range again at each end.
VARYING_ROOM = 0.2
# The share of the times a gap was seen that its least and most are made to hold.
USUAL_SHARE = 0.97
# A link is left out if the ones already chosen place its change within this of as well.
IMPLIED_WITHIN_S = 1.5
# Where to look for a line's traffic starting after a change, and stopping around one.
FLOW_AFTER_S = 40.0
FLOW_AROUND_S = (-40.0, 15.0)
# How long after a moment the estimator is asked about it: at once, and when a passage
# that crossed then might close.
LOOKS_S = (0.0, 30.0)


def read_changes(path: Path, start: float, end: float) -> list[HeadChange]:
    last: dict[str, SignalState] = {}
    changes = []
    with path.open(encoding="utf-8") as file:
        for head, seconds, state in csv.reader(file):
            now = SignalState(state)
            if start <= float(seconds) < end:
                changes.append((float(seconds), head, last.get(head), now))
            last[head] = now
    return changes


def read_crossings(path: Path, start: float, end: float) -> list[tuple[float, str]]:
    with path.open(encoding="utf-8") as file:
        rows = [(float(seconds), line) for line, seconds in csv.reader(file)]
    return sorted(row for row in rows if start <= row[0] < end)


def group_changes(config: SiteConfig, changes: Sequence[HeadChange]) -> dict[Change, list[float]]:
    """When each group's green started and ended, one time for each occasion."""
    plan = _plan(config)
    group_of = {head: name for name, group in plan.groups.items() for head in group.heads}
    times: dict[Change, list[float]] = {}
    for when, head, was, now in changes:
        if head not in group_of:
            continue
        edge = edge_of(was, now, len(config.signal_heads[head].lamps) == 3)
        if edge is None:
            continue
        seen = times.setdefault((group_of[head], edge), [])
        if not seen or when - seen[-1] > SAME_CHANGE_S:
            seen.append(when)
    return times


def gaps(first: Sequence[float], second: Sequence[float]) -> np.ndarray:
    """From each of the first to the next of the second, where that is in the same cycle."""
    found = []
    for when in first:
        index = bisect.bisect_right(second, when)
        if index < len(second) and second[index] - when < SAME_CYCLE_S:
            found.append(second[index] - when)
    return np.array(found)


def usual(seen: np.ndarray) -> tuple[Span, float]:
    """The least and most a gap usually is, and the share of the times seen that fall within.

    It is the narrowest range that holds nearly all of them. The rest are mostly a change
    that was missed or misread, which pairs one cycle's change with another's.
    """
    ordered = np.sort(seen)
    held = max(1, int(np.ceil(USUAL_SHARE * len(ordered))))
    widths = ordered[held - 1 :] - ordered[: len(ordered) - held + 1]
    first = int(np.argmin(widths))
    low, high = float(ordered[first]), float(ordered[first + held - 1])
    # A gap that varies with traffic has been seen only as far as the traffic so far took it.
    room = MARGIN_S if high - low <= FIXED_WIDTH_S else VARYING_ROOM * (high - low)
    span = (max(0.0, round(low - room, 1)), round(high + room, 1))
    return span, float(np.mean((seen >= span[0]) & (seen <= span[1])))


def learn(config: SiteConfig, changes: Sequence[HeadChange]) -> None:
    times = group_changes(config, changes)
    offered = []
    for first, first_times in times.items():
        for second, second_times in times.items():
            seen = gaps(first_times, second_times)
            if first != second and len(seen) >= MIN_SEEN:
                span, inside = usual(seen)
                offered.append((span[1] - span[0], first, second, span, len(seen), inside))
    offered.sort(key=lambda each: each[0])

    # Kept in this order: each group's own green, start to end, because its length is asked
    # for directly; every fixed gap that the ones before do not already give; then gaps that
    # vary, only where two changes are not yet joined at all.
    own = [each for each in offered if each[1][0] == each[2][0] and each[1][1] == ON]
    fixed = [each for each in offered if each not in own and each[0] <= FIXED_WIDTH_S]
    varying = [each for each in offered if each not in own and each not in fixed]
    chosen: list[SignalLink] = []
    kept = []
    for each in [*own, *fixed, *varying]:
        width, first, second, span, *_ = each
        reach = PlanTimings(SignalPlan(groups=_plan(config).groups, links=chosen)).reach
        already = reach.get((first, second))
        wanted = already is None or (
            each in fixed and already[1] - already[0] > width + IMPLIED_WITHIN_S
        )
        if each in own or wanted:
            link = {"from": _name(first), "to": _name(second), "s": span}
            chosen.append(SignalLink.model_validate(link))
            kept.append(each)

    print(f"  # From {len(changes)} changes of state. Each link: seconds from one group's green")
    print("  # starting (.on) or ending (.off) to another's, least and most.")
    print("  links:")
    for _, first, second, span, count, inside in sorted(kept, key=lambda each: each[1:3]):
        link = f"{{ from: {_name(first)}, to: {_name(second)}, s: [{span[0]}, {span[1]}] }}"
        print(f"    - {link:66} # seen {count}, {100 * inside:.1f}% within")


def flow(
    config: SiteConfig, changes: Sequence[HeadChange], crossings: Sequence[tuple[float, str]]
) -> None:
    """For a group with no head in view: when its traffic starts and stops crossing its line,
    against each change that can be seen. The ones that vary least are what to link it by."""
    times = group_changes(config, changes)
    for name, group in _plan(config).groups.items():
        for line in [] if group.heads else group.controls:
            crossed = [when for when, crossed_line in crossings if crossed_line == line]
            print(f"{name}, by {len(crossed)} crossings of {line}. Seconds from each change to:")
            print(f"  {'':18} {'the first crossing after it':>30} {'the last one around it':>30}")
            print(f"  {'':18} {'2%    10%    50%':>30} {'50%    90%    98%':>30}")
            for change, whens in sorted(times.items()):
                first, last = [], []
                for when in whens:
                    after = bisect.bisect_right(crossed, when)
                    if after < len(crossed) and crossed[after] - when < FLOW_AFTER_S:
                        first.append(crossed[after] - when)
                    around = bisect.bisect_right(crossed, when + FLOW_AROUND_S[1]) - 1
                    if around >= 0 and crossed[around] - when > FLOW_AROUND_S[0]:
                        last.append(crossed[around] - when)
                firsts, lasts = _shares(first, [2, 10, 50]), _shares(last, [50, 90, 98])
                print(f"  {_name(change):18} {firsts:>30} {lasts:>30}")


class _Truth:
    """What each head showed at any moment, from its changes."""

    def __init__(self, changes: Sequence[HeadChange]) -> None:
        self._when: dict[str, list[float]] = {}
        self._state: dict[str, list[SignalState]] = {}
        for when, head, _, now in changes:
            self._when.setdefault(head, []).append(when)
            self._state.setdefault(head, []).append(now)

    def of(self, heads: Sequence[str], at: float) -> SignalState | None:
        """What the heads agree on; None if they disagree or none is known."""
        states = set()
        for head in heads:
            index = bisect.bisect_right(self._when.get(head, []), at) - 1
            if index >= 0 and self._state[head][index] != SignalState.unknown:
                states.add(self._state[head][index])
        return states.pop() if len(states) == 1 else None


class _Feed:
    """Shows an estimator the recorded changes, as far as a moment at a time."""

    def __init__(self, config: SiteConfig, changes: Sequence[HeadChange]) -> None:
        self.estimator = StageSequenceEstimator(config)
        self._changes = changes
        self._next = 0

    def up_to(self, moment: float) -> StageSequenceEstimator:
        while self._next < len(self._changes) and self._changes[self._next][0] <= moment:
            when, head, was, now = self._changes[self._next]
            self.estimator.observe(head, was, now, datetime.fromtimestamp(when, UTC))
            self._next += 1
        return self.estimator


def evaluate(
    config: SiteConfig, changes: Sequence[HeadChange], crossings: Sequence[tuple[float, str]]
) -> None:
    truth = _Truth(changes)
    start, end = changes[0][0], changes[-1][0]
    print(f"{(end - start) / 3600:.1f} hours, {len(changes)} changes of state\n")
    print("Each group hidden from the estimator in turn, and what it made of it, second by")
    print("second: asked as the moment passes ('at once'), and 30 s on ('later'), as for a")
    print("passage that closes then. Then how well each change of the group was placed.")
    for name, group in _plan(config).groups.items():
        if not group.heads:
            continue
        shown = [change for change in changes if change[1] not in group.heads]
        for look in LOOKS_S:
            seconds = _hidden(config, name, group.heads, shown, truth, np.arange(start, end), look)
            print(f"  {name:11} {'at once' if look == 0 else 'later  '} {_scores(seconds)}")
            wrong = [
                (pair, n)
                for pair, n in seconds.most_common()
                if pair[1] not in (pair[0], NOT_KNOWN)
            ]
            if look and wrong:
                said = ", ".join(
                    f"{was.value} called {now.value} {n} s" for (was, now), n in wrong[:4]
                )
                print(f"              wrong: {said}")
        own = group_changes(config, [change for change in changes if change[1] in group.heads])
        for edge in (ON, OFF):
            print(f"              green {edge:3}: {_placing(config, name, edge, shown, own)}")

    print(
        "\nStop-line crossings, and how the line's signal at that moment is known (asked 30 s on):"
    )
    feed = _Feed(config, changes)
    counts: dict[str, Counter[str]] = {}
    for when, line in crossings:
        at = when - config.lines[line].lag_s
        estimator = feed.up_to(at + LOOKS_S[-1])
        how = "read from its heads"
        if truth.of(config.heads_controlling(line), at) is None:
            state, _ = estimator.state_of(line, datetime.fromtimestamp(at, UTC)) or UNKNOWN
            how = "not known" if state == NOT_KNOWN else f"inferred {state.value}"
        counts.setdefault(line, Counter())[how] += 1
    for line, counted in counts.items():
        shares = ", ".join(
            f"{how} {100 * n / counted.total():.1f}%" for how, n in counted.most_common()
        )
        print(f"  {line:26} {counted.total():5d}: {shares}")
    print(f"\nChanges that came outside a fixed gap: {feed.estimator.violations}")


def _hidden(
    config: SiteConfig,
    name: str,
    heads: Sequence[str],
    shown: Sequence[HeadChange],
    truth: _Truth,
    moments: np.ndarray,
    look: float,
) -> Counter[tuple[SignalState, SignalState]]:
    """Seconds by what a hidden group's heads showed and what the estimator said of it."""
    feed = _Feed(config, shown)
    seconds: Counter[tuple[SignalState, SignalState]] = Counter()
    for moment in moments.tolist():
        actual = truth.of(heads, moment)
        if actual is not None:
            estimator = feed.up_to(moment + look)
            said, _ = estimator.state_of_group(name, datetime.fromtimestamp(moment, UTC))
            seconds[(actual, said)] += 1
    return seconds


def _scores(seconds: Counter[tuple[SignalState, SignalState]]) -> str:
    total = seconds.total()
    given = sum(n for (_, said), n in seconds.items() if said != NOT_KNOWN)
    right = sum(n for (actual, said), n in seconds.items() if said == actual)
    by_state = []
    for state in (SignalState.green, SignalState.amber, SignalState.red, SignalState.red_amber):
        shown = sum(n for (actual, _), n in seconds.items() if actual == state)
        if shown:
            by_state.append(f"{state.value} {100 * seconds[(state, state)] / shown:.0f}%")
    return (
        f"given {100 * given / total:5.1f}% of {total / 3600:4.1f} h,"
        f" right {100 * right / max(given, 1):6.2f}% of that;"
        f" share of each state given and right: {', '.join(by_state)}"
    )


def _placing(
    config: SiteConfig,
    name: str,
    edge: str,
    shown: Sequence[HeadChange],
    actual: dict[Change, list[float]],
) -> str:
    """How well a hidden group's changes were placed, a minute after each happened."""
    feed = _Feed(config, shown)
    widths, misses, unplaced = [], [], 0
    for when in actual.get((name, edge), []):
        placed = feed.up_to(when + 60.0).placed(name, edge, datetime.fromtimestamp(when, UTC))
        near = [w for w in placed if w.earliest - NEAR_S <= when <= w.latest + NEAR_S]
        if not near:
            unplaced += 1
            continue
        widths.append(near[0].latest - near[0].earliest)
        misses.append(max(near[0].earliest - when, when - near[0].latest, 0.0))
    if not widths:
        return "none placed"
    inside = 100 * float(np.mean(np.array(misses) == 0))
    return (
        f"{len(widths)} placed, {unplaced} not; to within {np.median(widths):.1f} s"
        f" (9 in 10 within {np.percentile(widths, 90):.1f} s); {inside:.1f}% fell where"
        f" placed, the worst {max(misses):.1f} s outside"
    )


def _shares(seen: Sequence[float], percentiles: Sequence[float]) -> str:
    if len(seen) < MIN_SEEN:
        return "too few"
    return "  ".join(f"{value:5.1f}" for value in np.percentile(seen, percentiles))


def _plan(config: SiteConfig) -> SignalPlan:
    if config.signal_plan is None:
        sys.exit("signal_plan is not configured: give it its groups first")
    return config.signal_plan


def _name(change: Change) -> str:
    return f"{change[0]}.{change[1]}"


def _moment(text: str) -> float:
    return datetime.fromisoformat(text).astimezone(UTC).timestamp()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Learn the plan of the signals, and test the estimator."
    )
    parser.add_argument("command", choices=["learn", "flow", "evaluate"])
    parser.add_argument("--config", type=Path, required=True, help="path to site.yaml")
    parser.add_argument("--changes", type=Path, required=True, help="head, seconds, state")
    parser.add_argument("--crossings", type=Path, help="line, seconds")
    parser.add_argument(
        "--from", dest="start", type=_moment, default=0.0, help="use what follows this time"
    )
    parser.add_argument(
        "--until", dest="end", type=_moment, default=float("inf"), help="and precedes this one"
    )
    args = parser.parse_args()
    try:
        config, _ = load_site_config(args.config)
    except ConfigError as error:
        sys.exit(f"invalid config: {error}")
    changes = read_changes(args.changes, args.start, args.end)
    if not changes:
        sys.exit("no changes in that time")
    if args.command == "learn":
        learn(config, changes)
        return
    if args.crossings is None:
        sys.exit("--crossings is needed")
    crossings = [
        c for c in read_crossings(args.crossings, args.start, args.end) if c[1] in config.lines
    ]
    if args.command == "flow":
        flow(config, changes, crossings)
    else:
        evaluate(config, changes, crossings)


if __name__ == "__main__":
    main()
