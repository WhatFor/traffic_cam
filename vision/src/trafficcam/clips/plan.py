"""Which clips are wanted: triggers become windows of time, and overlapping ones share a clip."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from trafficcam.contracts import ClipTrigger


@dataclass(slots=True)
class PlannedClip:
    id: uuid.UUID
    start: datetime
    end: datetime
    triggers: list[ClipTrigger]


class ClipPlan:
    """The clips not yet finished. Not thread-safe: the recorder holds a lock around it."""

    def __init__(self, max_s: float) -> None:
        self._max = timedelta(seconds=max_s)
        self.open: list[PlannedClip] = []

    def add(self, trigger: ClipTrigger, pre_s: float, post_s: float) -> PlannedClip:
        """The clip that will show this trigger: an open one extended, or a new one."""
        start = trigger.at - timedelta(seconds=pre_s)
        end = trigger.at + timedelta(seconds=post_s)
        for clip in reversed(self.open):
            limit = clip.start + self._max
            # Joining must not cost the trigger its lead-in, and a clip cannot grow backwards.
            if clip.start <= start <= clip.end and trigger.at <= limit:
                clip.end = min(max(clip.end, end), limit)
                clip.triggers.append(trigger)
                clip.triggers.sort(key=lambda each: each.at)
                return clip
        clip = PlannedClip(uuid.uuid4(), start, end, [trigger])
        self.open.append(clip)
        return clip

    def close(self, clip: PlannedClip) -> None:
        self.open.remove(clip)
