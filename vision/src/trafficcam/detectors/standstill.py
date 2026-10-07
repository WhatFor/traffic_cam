"""Standing still: how long a track's ground point has stayed where it stopped."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np


@dataclass(slots=True)
class Spell:
    """A period in which the ground point stayed near where the period began."""

    started: datetime
    until: datetime
    x: float  # full-frame pixels
    y: float

    @property
    def duration(self) -> timedelta:
        return self.until - self.started


@dataclass(slots=True)
class _Track:
    current: Spell | None = None
    longest: Spell | None = None


class Standstills:
    """Follows each track's spells of standing within `radius_px` of one spot.

    A spell lasts while the point stays within the radius of where the spell began; moving
    further starts a new one. There is no map from pixels to metres everywhere a vehicle
    can stand, so the radius is in pixels.
    """

    def __init__(self, radius_px: float) -> None:
        self._radius = radius_px
        self._tracks: dict[int, _Track] = {}

    def at(self, track_id: int, timestamp: datetime, x: float, y: float) -> Spell:
        """Note where a track is. Returns the spell it is in, which may have just begun."""
        track = self._tracks.setdefault(track_id, _Track())
        spell = track.current
        if spell is not None and np.hypot(x - spell.x, y - spell.y) <= self._radius:
            spell.until = timestamp
        else:
            spell = track.current = Spell(timestamp, timestamp, float(x), float(y))
        if track.longest is None or spell.duration > track.longest.duration:
            track.longest = spell
        return spell

    def away(self, track_id: int) -> None:
        """The track is not where standing counts: its spell, if it had one, is over."""
        if track_id in self._tracks:
            self._tracks[track_id].current = None

    def take(self, track_id: int) -> Spell | None:
        """A finished track's longest spell. The track is forgotten."""
        track = self._tracks.pop(track_id, None)
        return None if track is None else track.longest
