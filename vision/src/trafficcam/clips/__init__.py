"""Clips: short recordings cut from the encoder's output around a trigger.

The encoder's recent output is held in memory, because a trigger usually arrives well
after the moment it is about: an event is raised when its passage closes.
"""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from trafficcam.contracts import Clip, ClipCommand, ClipDeleted, ClipKeep, Event

ClipRecord = Clip | ClipDeleted


@dataclass(frozen=True, slots=True)
class Packet:
    seq: int
    pts_us: int  # the encoder's timestamp
    keyframe: bool
    data: bytes


class PacketRing:
    """The last `seconds` of encoded video. Filled by the encoder's thread, read by another."""

    def __init__(self, seconds: float) -> None:
        self._span_us = int(seconds * 1_000_000)
        self._packets: deque[Packet] = deque()
        self._lock = threading.Lock()
        self._next_seq = 0
        self._origin: datetime | None = None

    @property
    def has_origin(self) -> bool:
        return self._origin is not None

    def set_origin(self, origin: datetime) -> None:
        """Say what time the encoder's timestamp 0 was."""
        self._origin = origin

    def append(self, data: bytes, keyframe: bool, pts_us: int) -> None:
        with self._lock:
            self._packets.append(Packet(self._next_seq, pts_us, keyframe, data))
            self._next_seq += 1
            while pts_us - self._packets[0].pts_us > self._span_us:
                self._packets.popleft()

    def pts_of(self, at: datetime) -> int | None:
        """The encoder timestamp of a moment, once the origin is known."""
        if self._origin is None:
            return None
        return (at - self._origin) // timedelta(microseconds=1)

    def time_of(self, pts_us: int) -> datetime:
        if self._origin is None:
            raise ValueError("the ring has no origin yet")
        return self._origin + timedelta(microseconds=pts_us)

    def from_keyframe(self, pts_us: int) -> list[Packet]:
        """Everything held from the last keyframe at or before `pts_us`.

        If that moment is older than the ring, from the oldest keyframe held.
        """
        with self._lock:
            packets = list(self._packets)
        start = None
        for index, packet in enumerate(packets):
            if not packet.keyframe:
                continue
            if start is not None and packet.pts_us > pts_us:
                break
            start = index
        return [] if start is None else packets[start:]

    def after(self, seq: int) -> list[Packet]:
        """Everything held that is newer than packet `seq`."""
        with self._lock:
            return [packet for packet in self._packets if packet.seq > seq]


class ClipRecorder(Protocol):
    def for_event(self, event: Event) -> Event:
        """Start or extend a clip if the event calls for one; returns it with its clip id."""
        ...

    def command(self, command: ClipCommand) -> None:
        """Record a clip because somebody asked."""
        ...

    def keep(self, keep: ClipKeep) -> None:
        """Mark a clip to outlast the usual retention, or take the mark off."""
        ...

    def drain(self) -> list[ClipRecord]:
        """Clips finished, and clips deleted, since the last call."""
        ...

    def close(self) -> None: ...


class NullRecorder:
    """Records nothing: for replays and tests."""

    def for_event(self, event: Event) -> Event:
        return event

    def command(self, command: ClipCommand) -> None:
        pass

    def keep(self, keep: ClipKeep) -> None:
        pass

    def drain(self) -> list[ClipRecord]:
        return []

    def close(self) -> None:
        pass
