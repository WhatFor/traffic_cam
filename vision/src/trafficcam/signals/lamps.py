"""Reading signal heads from lamp colour samples.

See docs/spikes/lamp-readability.md for the measurements behind the numbers here.
"""

from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from trafficcam.config import LampColour, SignalHead, SiteConfig
from trafficcam.contracts import SignalState
from trafficcam.signals import Reading
from trafficcam.sources import Frame, Rgb

VOTE_FRAMES = 5
# A change that skips a step of the sequence, or a combination of lamps that means
# nothing, must last this long before it is believed. Shorter ones are a vehicle passing.
HOLD = timedelta(seconds=1)
# A head with fewer than three lamps has no sequence to check a change against, so every
# change on it has to last.
HOLD_UNCHECKED = timedelta(seconds=2)
# A lamp's lit and unlit levels are learned from this much of its own recent history, so
# they follow the light through the day. It spans several signal cycles.
LEVEL_WINDOW_S = 300
# A lamp's levels are trusted once its history falls into two groups this far apart, with
# few readings in the middle third between them and neither group too small to be a state
# of the lamp. In daylight a lit lamp scores 25 to 100 above an unlit one, at night 100 to
# 200. A lamp that has only been seen unlit has one group, or a second made of a few
# readings from something passing behind it.
MIN_SWING = 15.0
MAX_MIDDLE_SHARE = 0.1
MIN_GROUP_SHARE = 0.02
# A lamp also has to swing at least this share of what the strongest lamp on its head
# does. At night the glow of the lamp next to it moves an unlit lamp's score by a tenth
# of that; without this, that glow passes for the lamp being lit until it really is.
MIN_SHARE_OF_STRONGEST = 0.25

SEQUENCE = {
    SignalState.red: SignalState.red_amber,
    SignalState.red_amber: SignalState.green,
    SignalState.green: SignalState.amber,
    SignalState.amber: SignalState.red,
}
STATES: dict[frozenset[str], SignalState] = {
    frozenset({"red"}): SignalState.red,
    frozenset({"red", "amber"}): SignalState.red_amber,
    frozenset({"green"}): SignalState.green,
    frozenset({"amber"}): SignalState.amber,
}


def lamp_score(rgb: Rgb, colour: str) -> float:
    """How lit a lamp looks, from its mean colour.

    A lit red is dim but strongly red, so brightness alone confuses it with a pale vehicle
    passing behind the head. Amber needs some green as well as red, or the night-time glow
    of the red lamp just above it counts; only some, because in daylight a lit amber shows
    as a dull red with half as much green. Green washes out towards white, so it is scored
    mostly on brightness.
    """
    r, g, b = rgb
    if colour == "red":
        return r - max(g, b)
    if colour == "amber":
        return min(r, 2 * g) - b / 2
    return g - r / 2


class LampLevels:
    """Learns one lamp's lit and unlit scores, and says which a new score is."""

    def __init__(self, fps: int) -> None:
        self._scores: deque[float] = deque(maxlen=LEVEL_WINDOW_S * fps)
        self._refresh_every = fps
        self._low = self._high = 0.0
        # Whether the history shows two states. The head decides whether to trust them.
        self.separated = False

    @property
    def swing(self) -> float:
        return self._high - self._low

    def add(self, score: float) -> None:
        self._scores.append(score)
        if len(self._scores) % self._refresh_every == 1:
            self._learn(np.fromiter(self._scores, dtype=np.float32))

    def _learn(self, scores: np.ndarray) -> None:
        """Split the history into an unlit and a lit group, and take each one's middle.

        Percentiles alone would do if the history held nothing else. It does: a dark
        vehicle behind a lamp reads below unlit and a pale one above lit, for a few
        seconds at a time. The middle of each group ignores them.
        """
        low, high = (float(level) for level in np.percentile(scores, [2, 99]))
        smaller = 0
        for _ in range(3):
            lit = scores > (low + high) / 2
            if not lit.any() or lit.all():
                break
            low, high = float(np.median(scores[~lit])), float(np.median(scores[lit]))
            smaller = int(min(lit.sum(), len(scores) - lit.sum()))
        self._low, self._high = low, high
        swing = high - low
        middle = (scores > low + swing / 3) & (scores < high - swing / 3)
        self.separated = (
            swing >= MIN_SWING
            # A second of readings at the least, and more as the history grows.
            and smaller >= max(self._refresh_every, MIN_GROUP_SHARE * len(scores))
            and middle.mean() <= MAX_MIDDLE_SHARE
        )

    def margin(self, score: float) -> float:
        """Signed distance from the threshold, as a share of half the swing: above 0 is lit."""
        half = (self._high - self._low) / 2
        return (score - (self._low + half)) / half


def state_for(lit: frozenset[str], lamps: frozenset[str]) -> SignalState:
    """The state a set of lit lamps means, on a head that has `lamps`."""
    if lit in STATES:
        return STATES[lit]
    # With no red lamp to look at, a head that is not green is taken to be red.
    if not lit and "red" not in lamps:
        return SignalState.red
    return SignalState.unknown


@dataclass(slots=True)
class _Head:
    lamps: dict[LampColour, LampLevels]
    recent: deque[SignalState] = field(default_factory=lambda: deque(maxlen=VOTE_FRAMES))
    state: SignalState = SignalState.unknown
    since: datetime | None = None
    pending: tuple[SignalState, datetime] | None = None

    def wait_before(self, new: SignalState) -> timedelta:
        """How long a change to `new` must last before it is believed."""
        if new == SignalState.unknown:
            return HOLD
        if len(self.lamps) < 3:
            return HOLD_UNCHECKED
        if self.state == SignalState.unknown or SEQUENCE.get(self.state) == new:
            return timedelta()
        return HOLD


class LampRoiObserver:
    """Reads every configured head from the lamp samples a frame carries."""

    def __init__(self, config: SiteConfig) -> None:
        self._config = config.signal_heads
        self._heads = {
            name: _Head({colour: LampLevels(config.camera.fps) for colour in head.lamps})
            for name, head in config.signal_heads.items()
        }

    def read(self, frame: Frame) -> Mapping[str, Reading]:
        if not frame.samples:
            return {}
        return {
            name: self._read(name, head, self._config[name], frame)
            for name, head in self._heads.items()
        }

    def _read(self, name: str, head: _Head, config: SignalHead, frame: Frame) -> Reading:
        scores = {}
        for colour, levels in head.lamps.items():
            scores[colour] = lamp_score(frame.samples[f"{name}/{colour}"], colour)
            levels.add(scores[colour])
        strongest = max((lamp.swing for lamp in head.lamps.values() if lamp.separated), default=0.0)
        margins = {
            colour: levels.margin(scores[colour])
            for colour, levels in head.lamps.items()
            if levels.separated and levels.swing >= MIN_SHARE_OF_STRONGEST * strongest
        }
        # A head with a lamp that cannot be told lit from unlit is not read at all: a hooded
        # red in daylight, or any lamp that has not yet shown both states.
        seen, confidence = SignalState.unknown, None
        if len(margins) == len(head.lamps):
            lit = frozenset(colour for colour, margin in margins.items() if margin > 0)
            seen = state_for(lit, frozenset(config.lamps))
            confidence = min(1.0, min(abs(margin) for margin in margins.values()))
        head.recent.append(seen)
        voted = Counter(head.recent).most_common(1)[0][0]

        if head.since is None:
            head.since = frame.timestamp
        if voted == head.state:
            head.pending = None
        else:
            if head.pending is None or head.pending[0] != voted:
                head.pending = (voted, frame.timestamp)
            _, first_seen = head.pending
            if frame.timestamp - first_seen >= head.wait_before(voted):
                head.state, head.since, head.pending = voted, first_seen, None
        return Reading(head.state, head.since, confidence)
