"""The watchdog pings systemd from the frame loop, no more often than it needs to."""

import pytest

from trafficcam.health.watchdog import Watchdog, systemd_notify


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_it_pings_at_once_and_then_three_times_per_timeout() -> None:
    sent: list[str] = []
    clock = Clock()
    watchdog = Watchdog(sent.append, timeout_s=30, clock=clock)

    watchdog.ping()
    clock.now += 9.9
    watchdog.ping()
    assert sent == ["WATCHDOG=1"]

    clock.now += 0.1
    watchdog.ping()
    assert sent == ["WATCHDOG=1", "WATCHDOG=1"]


def test_without_a_timeout_it_never_pings() -> None:
    sent: list[str] = []
    watchdog = Watchdog(sent.append, timeout_s=None)

    watchdog.ping()

    assert sent == []


def test_the_timeout_comes_from_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    Watchdog.from_environment(sent.append).ping()
    monkeypatch.delenv("WATCHDOG_USEC")
    Watchdog.from_environment(sent.append).ping()

    assert sent == ["WATCHDOG=1"]


def test_outside_systemd_notifying_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)

    assert systemd_notify()("READY=1") is None
