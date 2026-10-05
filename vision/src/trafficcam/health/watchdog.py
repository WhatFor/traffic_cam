"""The systemd watchdog: pings from the frame loop, so a stalled loop gets restarted."""

import os
import time
from collections.abc import Callable
from typing import Self

from trafficcam.pipeline import FrameResult

Notify = Callable[[str], object]


def systemd_notify() -> Notify:
    """systemd's notify call when running under a notify unit, otherwise a no-op."""
    if "NOTIFY_SOCKET" not in os.environ:
        return lambda _message: None
    # Imported here because python3-systemd comes from apt and only exists on the Pi.
    from systemd import daemon  # pyright: ignore[reportMissingImports]

    return daemon.notify


class Watchdog:
    """Tells systemd the loop is alive, three times per timeout. No timeout, no pings."""

    def __init__(
        self, notify: Notify, timeout_s: float | None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._notify = notify
        self._interval = None if timeout_s is None else timeout_s / 3
        self._clock = clock
        self._next_ping = 0.0

    @classmethod
    def from_environment(cls, notify: Notify) -> Self:
        # systemd sets this to the unit's WatchdogSec, in microseconds.
        timeout_usec = os.environ.get("WATCHDOG_USEC")
        return cls(notify, int(timeout_usec) / 1e6 if timeout_usec else None)

    def ping(self) -> None:
        if self._interval is None:
            return
        now = self._clock()
        if now >= self._next_ping:
            self._notify("WATCHDOG=1")
            self._next_ping = now + self._interval

    def observe(self, result: FrameResult) -> None:
        self.ping()

    def close(self) -> None:
        pass
