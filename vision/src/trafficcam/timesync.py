"""Waiting for the system clock to be synchronised."""

import subprocess
import time
from collections.abc import Callable

LOG_EVERY_S = 30


def clock_is_synchronised() -> bool:
    result = subprocess.run(
        ["timedatectl", "show", "--property", "NTPSynchronized", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "yes"


def wait_for_clock_sync(
    synchronised: Callable[[], bool] = clock_is_synchronised,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 2.0,
    waiting: Callable[[], None] = lambda: None,
) -> None:
    """Block until the clock is synchronised, calling `waiting` on every poll.

    The Pi has no battery-backed clock, so after a power cut its time is wrong until NTP
    catches up, and anything stamped before then would carry the wrong time.
    """
    waited = 0.0
    while not synchronised():
        if waited % LOG_EVERY_S == 0:
            print("waiting for the clock to synchronise", flush=True)
        waiting()
        sleep(poll_s)
        waited += poll_s
