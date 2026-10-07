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
from typing import NamedTuple, Protocol

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
# How far outside what the plan allows a change may be seen before it counts as outside.
SLACK_S = 0.5
# Only a gap fixed this tightly can show that the plan no longer holds.
FIXED_WIDTH_S = 5.0
REMEMBER_S = 15 * 60.0
# A change placed no better than this is worth looking for in the lamps themselves.
LOOSE_S = 2.0
# How closely a change found from the lamps' steps is taken to be placed, either side.
FOUND_WITHIN_S = 0.6
# Stands for the lamps' steps where a head's name would be.
STEPS = "(steps)"
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


class Loose(NamedTuple):
    """A change of a group that the plan places only loosely."""

    group: str
    edge: str
    heads: list[str]
    earliest: datetime
    latest: datetime


class Placed(NamedTuple):
    """A state worked out for a moment."""

    state: SignalState
    since: datetime | None = None  # the latest it can have begun
    # How closely the change that began it is placed, in seconds: the doubt in `since`.
    within_s: float | None = None


UNKNOWN = Placed(SignalState.unknown)


class PhaseEstimator(Protocol):
    def observe(self, head: str, was: SignalState | None, now: SignalState, at: datetime) -> None:
        """Take note of a head's state changing, as read from its lamps."""
        ...

    def forget(self, head: str) -> None:
        """Take back what a head was seen to do: its readings are no longer believed."""
        ...

    def loosely_placed(self, around: datetime) -> list[Loose]:
        """The changes near a moment that are placed only loosely, for looking for."""
        ...

    def found(self, group: str, edge: str, at: datetime) -> None:
        """Take a change as having been found at a moment."""
        ...

    def state_of(self, target: str, at: datetime) -> Placed | None:
        """The state of whatever controls a line or movement; None if nothing in the plan
        controls it."""
        ...

    def state_for_head(self, head: str, at: datetime) -> Placed | None:
        """What a head should be showing, going only by heads of other groups; None if the
        head is in no group."""
        ...


class NullEstimator:
    """Works nothing out: for a site with no plan."""

    def observe(self, head: str, was: SignalState | None, now: SignalState, at: datetime) -> None:
        pass

    def forget(self, head: str) -> None:
        pass

    def loosely_placed(self, around: datetime) -> list[Loose]:
        return []

    def found(self, group: str, edge: str, at: datetime) -> None:
        pass

    def state_of(self, target: str, at: datetime) -> Placed | None:
        return None

    def state_for_head(self, head: str, at: datetime) -> Placed | None:
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
        # From one change to each occasion of another that links lead to: the one before it,
        # the one after. For each occasion, the run of links that places it best.
        every: dict[tuple[Change, Change], list[Span]] = {}
        for start in self.changes:
            self._walk(start, start, (0.0, 0.0), {start}, both, every)
        self.reach: dict[tuple[Change, Change], list[Span]] = {}
        for pair, spans in every.items():
            kept: list[Span] = []
            for span in sorted(spans, key=lambda each: each[1] - each[0]):
                # One that overlaps a narrower one is the same occasion, placed worse.
                if all(span[1] < other[0] or span[0] > other[1] for other in kept):
                    kept.append(span)
            self.reach[pair] = kept
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
        found: dict[tuple[Change, Change], list[Span]],
    ) -> None:
        found.setdefault((start, here), []).append(span)
        for there, step in links.get(here, []):
            onward = (span[0] + step[0], span[1] + step[1])
            if there not in visited and onward[1] - onward[0] <= MAX_WIDTH_S:
                self._walk(start, there, onward, visited | {there}, links, found)

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
        self._heads_of_group = {name: list(group.heads) for name, group in plan.groups.items()}
        self._group_of_target: dict[str, str] = {}
        for name, group in plan.groups.items():
            targets = [
                *group.controls,
                *(t for head in group.heads for t in config.signal_heads[head].controls),
            ]
            for target in targets:
                self._group_of_target[target] = name
        # What was seen, oldest first, each with the heads it was seen on.
        self._seen: dict[Change, list[tuple[Window, set[str]]]] = {}
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
                seen
                for seen in self._seen.get((group, OFF), [])
                if moment - seen[0].latest > NEAR_S
            ]
            return
        edge = edge_of(was, now, self._full_head[head])
        if edge is not None:
            self._note((group, edge), moment, head)

    def forget(self, head: str) -> None:
        for change, seen in self._seen.items():
            self._seen[change] = [(window, heads) for window, heads in seen if heads != {head}]

    def state_of(self, target: str, at: datetime) -> Placed | None:
        group = self._group_of_target.get(target)
        return None if group is None else self.state_of_group(group, at)

    def state_for_head(self, head: str, at: datetime) -> Placed | None:
        group = self._group_of_head.get(head)
        return None if group is None else self.state_of_group(group, at, by_others=True)

    def state_of_group(self, group: str, at: datetime, *, by_others: bool = False) -> Placed:
        """A group's state at a moment. `by_others` leaves out what its own heads were seen
        to do, which is how to check them."""
        moment = at.timestamp()
        leaving_out = group if by_others else None
        ons = self._windows((group, ON), moment, leaving_out)
        offs = self._windows((group, OFF), moment, leaving_out)
        amber_s, red_amber_s = (AMBER_S, RED_AMBER_S) if self._full[group] else (0.0, 0.0)

        def answer(state: SignalState, since: float, change: Window) -> Placed:
            return Placed(
                state, datetime.fromtimestamp(since, UTC), change.latest - change.earliest
            )

        began = _last(window for window in ons if window.latest <= moment)
        ended = _last(window for window in offs if window.latest <= moment)
        if began is not None and (ended is None or began.latest > ended.latest):
            # Green has begun. It lasts at least as long as the plan says, and longer if its
            # end has been placed later than that.
            length = self._timings.following.get(((group, ON), (group, OFF)))
            if length is None:
                return UNKNOWN
            until = began.earliest + length[0]
            coming = _first(w for w in offs if w.latest > moment and w.earliest >= began.earliest)
            if coming is not None and coming.earliest <= began.latest + length[1]:
                until = max(until, coming.earliest)
            return answer(SignalState.green, began.latest, began) if moment < until else UNKNOWN
        if ended is None:
            return UNKNOWN
        if amber_s and moment < ended.earliest + amber_s:
            return answer(SignalState.amber, ended.latest, ended)
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
                return answer(SignalState.red_amber, coming.latest - red_amber_s, coming)
            red_until.append(coming.earliest - red_amber_s)
        if red_until and moment < max(red_until):
            return answer(SignalState.red, red_from, ended)
        return UNKNOWN

    def loosely_placed(self, around: datetime) -> list[Loose]:
        """The changes near a moment, of groups with heads, that are worth looking for."""
        moment = around.timestamp()
        return [
            Loose(
                group,
                edge,
                heads,
                datetime.fromtimestamp(window.earliest, UTC),
                datetime.fromtimestamp(window.latest, UTC),
            )
            for group, heads in self._heads_of_group.items()
            for edge in (ON, OFF)
            for window in self._windows((group, edge), moment)
            if heads and window.latest - window.earliest > LOOSE_S
        ]

    def found(self, group: str, edge: str, at: datetime) -> None:
        """Take a change as having been found in the lamps' steps at a moment."""
        moment = at.timestamp()
        window = Window(moment - FOUND_WITHIN_S, moment + FOUND_WITHIN_S)
        self._seen.setdefault((group, edge), []).append((window, {STEPS}))

    def placed(self, group: str, edge: str, around: datetime) -> list[Window]:
        """Where the changes seen so far put a group's green starting or ending, near a moment."""
        return self._windows((group, edge), around.timestamp())

    def _note(self, change: Change, moment: float, head: str) -> None:
        if not self._full_head[head] and self._ruled_out(change, moment):
            # A head with one lamp or two has nothing to check its own reading against. If
            # what a three-lamp head was seen to do leaves no room for this, it is a misreading.
            self.violations += 1
            return
        if self._breaks_the_plan(change, moment):
            self.violations += 1
        seen = self._seen.setdefault(change, [])
        if seen and moment - seen[-1][0].latest <= SAME_CHANGE_S:
            last, heads = seen[-1]
            seen[-1] = (
                Window(min(last.earliest, moment), max(last.latest, moment)),
                heads | {head},
            )
        else:
            seen.append((Window(moment, moment), {head}))
        for each in self._seen.values():
            while each and moment - each[0][0].latest > REMEMBER_S:
                del each[0]

    def _ruled_out(self, change: Change, moment: float) -> bool:
        """Whether a change at this moment comes sooner after, or sooner before, a change of
        a three-lamp head (or one found from the lamps' steps) than the plan lets it."""
        for other, seen in self._seen.items():
            if other[0] == change[0]:
                continue
            after = self._timings.following.get((other, change))
            before = self._timings.following.get((change, other))
            for window, heads in seen:
                if not any(head == STEPS or self._full_head[head] for head in heads):
                    continue
                too_soon_after = after is not None and (
                    0 <= moment - window.earliest and moment - window.latest < after[0] - SLACK_S
                )
                too_soon_before = before is not None and (
                    0 < window.latest - moment and window.earliest - moment < before[0] - SLACK_S
                )
                if too_soon_after or too_soon_before:
                    return True
        return False

    def _breaks_the_plan(self, change: Change, moment: float) -> bool:
        for other, seen in self._seen.items():
            if other == change:
                continue
            for span in self._timings.reach.get((other, change), []):
                if span[1] - span[0] > FIXED_WIDTH_S:
                    continue
                for window, _ in seen:
                    expected = window.shifted(span)
                    missed_by = max(expected.earliest - moment, moment - expected.latest)
                    if 0 < missed_by < NEAR_S:
                        return True
        return False

    def _windows(
        self, change: Change, around: float, leaving_out: str | None = None
    ) -> list[Window]:
        """Every place the changes seen put this one, near a moment, oldest first.

        `leaving_out` is a group whose own changes are not to count.
        """
        placed: list[tuple[Window, bool]] = []
        for other, seen in self._seen.items():
            if other[0] == leaving_out:
                continue
            for span in self._timings.reach.get((other, change), []):
                for window, _ in seen:
                    if abs(window.latest - around) <= REACH_S:
                        placed.append((window.shifted(span), other == change))
        placed.sort(key=lambda each: each[0].earliest)

        merged: list[tuple[Window, bool]] = []
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
                # Two places for one change that cannot both be right: it was at one of
                # them, or between.
                merged[-1] = (Window(last.earliest, max(last.latest, window.latest)), last_seen)
        return [window for window, _ in merged]


def _last(windows: Iterable[Window]) -> Window | None:
    return max(windows, key=lambda window: window.latest, default=None)


def _first(windows: Iterable[Window]) -> Window | None:
    return min(windows, key=lambda window: window.earliest, default=None)
