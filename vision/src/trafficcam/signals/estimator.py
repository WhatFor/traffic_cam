"""Signal states worked out for heads that cannot be read, from the plan of the signals.

The plan (`signal_plan` in the site config) says how long after one group's green starts or
ends another's does. Most of those gaps are fixed by the controller, so one head that can be
read places most of the cycle. Every change is carried as the earliest and latest it can
have happened, and a state is given only where those leave no doubt.
"""

import heapq
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from trafficcam.config import SignalPlan, SiteConfig, change_of
from trafficcam.contracts import SignalState

# What every UK signal shows for a fixed time on the way out of green and into it.
AMBER_S = 3.0
RED_AMBER_S = 2.0
# A change placed no better than this is not carried on to place another.
MAX_WIDTH_S = 30.0
# Heads of one group change within this of each other: one change, not two.
SAME_CHANGE_S = 1.0
# Two places for one change that miss each other by less than this cannot both be right.
NEAR_S = 10.0
# Only a gap fixed this tightly can show that the plan no longer holds.
FIXED_WIDTH_S = 5.0
REMEMBER_S = 15 * 60.0
# No link reaches further than this, so changes seen further off say nothing about a moment.
REACH_S = 200.0

ON, OFF = "on", "off"
SEQUENCE_LAMPS = ("red", "amber", "green")
Change = tuple[str, str]  # a group, and its green starting or ending
Span = tuple[float, float]  # least and most seconds


@dataclass(frozen=True, slots=True)
class Window:
    """When a change happened: no earlier and no later than these."""

    earliest: float
    latest: float

    def shifted(self, span: Span) -> "Window":
        return Window(self.earliest + span[0], self.latest + span[1])


UNKNOWN: tuple[SignalState, datetime | None] = (SignalState.unknown, None)


class PhaseEstimator(Protocol):
    def observe(self, head: str, was: SignalState | None, now: SignalState, at: datetime) -> None:
        """Take note of a head's state changing, as read from its lamps."""
        ...

    def state_of(self, target: str, at: datetime) -> tuple[SignalState, datetime | None] | None:
        """The state of whatever controls a line or movement, and since when; None if nothing
        in the plan controls it."""
        ...


class NullEstimator:
    """Works nothing out: for a site with no plan."""

    def observe(self, head: str, was: SignalState | None, now: SignalState, at: datetime) -> None:
        pass

    def state_of(self, target: str, at: datetime) -> tuple[SignalState, datetime | None] | None:
        return None


def edge_of(was: SignalState | None, now: SignalState, full_head: bool) -> str | None:
    """Whether a head's change is its green starting or ending, as a step of the sequence.

    A change to or from unknown is neither: a head coming back into view while green did not
    turn green at that moment.
    """
    before_green = SignalState.red_amber if full_head else SignalState.red
    after_green = SignalState.amber if full_head else SignalState.red
    if was == before_green and now == SignalState.green:
        return ON
    if was == SignalState.green and now == after_green:
        return OFF
    return None


class PlanTimings:
    """How far apart the plan puts any two changes, through as many links as it takes."""

    def __init__(self, plan: SignalPlan) -> None:
        forward: dict[Change, list[tuple[Change, Span]]] = {}
        both: dict[Change, list[tuple[Change, Span]]] = {}
        for link in plan.links:
            first, second = change_of(link.from_), change_of(link.to)
            if first is None or second is None:
                continue
            forward.setdefault(first, []).append((second, link.s))
            both.setdefault(first, []).append((second, link.s))
            both.setdefault(second, []).append((first, (-link.s[1], -link.s[0])))
        self.changes = sorted(
            {(group, edge) for group in plan.groups for edge in (ON, OFF)} | set(both)
        )
        # From one change to the nearest of another, along the run of links that places it best.
        self.reach: dict[tuple[Change, Change], Span] = {}
        for start in self.changes:
            self._walk(start, start, (0.0, 0.0), {start}, both)
        # From one change to the next of another after it: the soonest, and the latest by the
        # same route.
        self.following: dict[tuple[Change, Change], Span] = {}
        for start in self.changes:
            for end, span in self._soonest(start, forward).items():
                self.following[(start, end)] = span

    def _walk(
        self,
        start: Change,
        here: Change,
        span: Span,
        visited: set[Change],
        links: dict[Change, list[tuple[Change, Span]]],
    ) -> None:
        best = self.reach.get((start, here))
        if best is None or span[1] - span[0] < best[1] - best[0]:
            self.reach[(start, here)] = span
        for there, step in links.get(here, []):
            onward = (span[0] + step[0], span[1] + step[1])
            if there not in visited and onward[1] - onward[0] <= MAX_WIDTH_S:
                self._walk(start, there, onward, visited | {there}, links)

    @staticmethod
    def _soonest(
        start: Change, links: dict[Change, list[tuple[Change, Span]]]
    ) -> dict[Change, Span]:
        found: dict[Change, Span] = {}
        queue: list[tuple[float, float, Change]] = [
            (step[0], step[1], there) for there, step in links.get(start, [])
        ]
        heapq.heapify(queue)
        while queue:
            least, most, here = heapq.heappop(queue)
            if here in found:
                continue
            found[here] = (least, most)
            for there, step in links.get(here, []):
                if there not in found:
                    heapq.heappush(queue, (least + step[0], most + step[1], there))
        return found


class StageSequenceEstimator:
    """Places the changes it was not shown from the ones it was.

    It can be asked about the past: a change is often placed only by one that follows it,
    and a passage's signal is looked up when the passage closes.
    """

    def __init__(self, config: SiteConfig) -> None:
        plan = config.signal_plan
        if plan is None:
            raise ValueError("signal_plan is not configured")
        self._timings = PlanTimings(plan)
        self._group_of_head = {
            head: name for name, group in plan.groups.items() for head in group.heads
        }
        self._full_head = {
            name: len(head.lamps) == len(SEQUENCE_LAMPS)
            for name, head in config.signal_heads.items()
        }
        # A group shows amber and red-and-amber unless every head it has lacks the lamps.
        self._full = {
            name: not group.heads or any(self._full_head[head] for head in group.heads)
            for name, group in plan.groups.items()
        }
        self._group_of_target: dict[str, str] = {}
        for name, group in plan.groups.items():
            targets = [
                *group.controls,
                *(t for head in group.heads for t in config.signal_heads[head].controls),
            ]
            for target in targets:
                self._group_of_target[target] = name
        self._seen: dict[Change, list[Window]] = {}
        # Changes that came outside where a fixed gap from another put them.
        self.violations = 0

    def observe(self, head: str, was: SignalState | None, now: SignalState, at: datetime) -> None:
        group = self._group_of_head.get(head)
        if group is None:
            return
        moment = at.timestamp()
        if was == SignalState.amber and now == SignalState.green:
            # It never left green: something passed in front of it. Take the ending back.
            self._seen[(group, OFF)] = [
                window
                for window in self._seen.get((group, OFF), [])
                if moment - window.latest > NEAR_S
            ]
            return
        edge = edge_of(was, now, self._full_head[head])
        if edge is not None:
            self._note((group, edge), moment)

    def state_of(self, target: str, at: datetime) -> tuple[SignalState, datetime | None] | None:
        group = self._group_of_target.get(target)
        return None if group is None else self.state_of_group(group, at)

    def state_of_group(self, group: str, at: datetime) -> tuple[SignalState, datetime | None]:
        moment = at.timestamp()
        ons = self._windows((group, ON), moment)
        offs = self._windows((group, OFF), moment)
        amber_s, red_amber_s = (AMBER_S, RED_AMBER_S) if self._full[group] else (0.0, 0.0)

        def answer(state: SignalState, since: float) -> tuple[SignalState, datetime | None]:
            return state, datetime.fromtimestamp(since, UTC)

        began = _last(window for window in ons if window.latest <= moment)
        ended = _last(window for window in offs if window.latest <= moment)
        if began is not None and (ended is None or began.latest > ended.latest):
            # Green has begun. It lasts at least as long as the plan says, and longer if its
            # end has been placed later than that.
            length = self._timings.following.get(((group, ON), (group, OFF)))
            if length is None:
                return SignalState.unknown, None
            until = began.earliest + length[0]
            coming = _first(w for w in offs if w.latest > moment and w.earliest >= began.earliest)
            if coming is not None and coming.earliest <= began.latest + length[1]:
                until = max(until, coming.earliest)
            return answer(SignalState.green, began.latest) if moment < until else UNKNOWN
        if ended is None:
            return UNKNOWN
        if amber_s and moment < ended.earliest + amber_s:
            return answer(SignalState.amber, ended.latest)
        red_from = ended.latest + amber_s
        if moment < red_from:
            return UNKNOWN
        # Red lasts at least until the plan lets green come round again, and longer if the
        # next green has been placed later than that.
        gap = self._timings.following.get(((group, OFF), (group, ON)))
        red_until = [] if gap is None else [ended.earliest + gap[0] - red_amber_s]
        coming = _first(window for window in ons if window.latest > moment)
        if coming is not None and (gap is None or coming.earliest <= ended.latest + gap[1]):
            if red_amber_s and coming.latest - red_amber_s <= moment < coming.earliest:
                return answer(SignalState.red_amber, coming.latest - red_amber_s)
            red_until.append(coming.earliest - red_amber_s)
        if red_until and moment < max(red_until):
            return answer(SignalState.red, red_from)
        return UNKNOWN

    def placed(self, group: str, edge: str, around: datetime) -> list[Window]:
        """Where the changes seen so far put a group's green starting or ending, near a moment."""
        return self._windows((group, edge), around.timestamp())

    def _note(self, change: Change, moment: float) -> None:
        if self._breaks_the_plan(change, moment):
            self.violations += 1
        seen = self._seen.setdefault(change, [])
        if seen and moment - seen[-1].latest <= SAME_CHANGE_S:
            seen[-1] = Window(min(seen[-1].earliest, moment), max(seen[-1].latest, moment))
        else:
            seen.append(Window(moment, moment))
        for windows in self._seen.values():
            while windows and moment - windows[0].latest > REMEMBER_S:
                del windows[0]

    def _breaks_the_plan(self, change: Change, moment: float) -> bool:
        for other, windows in self._seen.items():
            span = self._timings.reach.get((other, change))
            if other == change or span is None or span[1] - span[0] > FIXED_WIDTH_S:
                continue
            for window in windows:
                expected = window.shifted(span)
                missed_by = max(expected.earliest - moment, moment - expected.latest)
                if 0 < missed_by < NEAR_S:
                    return True
        return False

    def _windows(self, change: Change, around: float) -> list[Window]:
        """Every place the changes seen put this one, near a moment, oldest first."""
        placed: list[tuple[Window, bool]] = []
        for seen, windows in self._seen.items():
            span = self._timings.reach.get((seen, change))
            if span is None:
                continue
            for window in windows:
                if abs(window.latest - around) <= REACH_S:
                    placed.append((window.shifted(span), seen == change))
        placed.sort(key=lambda each: each[0].earliest)

        merged: list[tuple[Window, bool]] = []
        doubted: set[int] = set()
        for window, was_seen in placed:
            if not merged or window.earliest - merged[-1][0].latest >= NEAR_S:
                merged.append((window, was_seen))
                continue
            last, last_seen = merged[-1]
            if window.earliest <= last.latest:
                both = Window(max(last.earliest, window.earliest), min(last.latest, window.latest))
                merged[-1] = (both, last_seen or was_seen)
            elif was_seen != last_seen:
                # What was seen is believed over what was worked out.
                merged[-1] = (window, True) if was_seen else (last, True)
            else:
                doubted.add(len(merged) - 1)
        return [window for index, (window, _) in enumerate(merged) if index not in doubted]


def _last(windows: Iterable[Window]) -> Window | None:
    return max(windows, key=lambda window: window.latest, default=None)


def _first(windows: Iterable[Window]) -> Window | None:
    return min(windows, key=lambda window: window.earliest, default=None)
