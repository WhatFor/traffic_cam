"""The state of each group of signal heads, as records to publish.

A group's state is not always known as it happens: a change found from the lamps' steps is
known five seconds later, and one placed by a later change up to three quarters of a minute
later (ADR 0022). So each group is told twice. *Live* is the state as known at once, which
is `unknown` for the moments that cannot be known yet. *Settled* is the state as known some
time on, by when everything that will be learned about a moment has been: it is the record,
in order and never revised.
"""

import uuid
from datetime import datetime, timedelta

from trafficcam.config import SiteConfig
from trafficcam.contracts import GroupState, SignalSource, SignalState
from trafficcam.signals import Signals

# How long after a moment its state is taken as settled. The last thing to be learned about
# a moment is the end of the ahead green from the end of the slip lane's, 33 s later, itself
# known 9 s after it happens from the pedestrian crossing.
SETTLE = timedelta(seconds=45)
# A group's state is asked for this often: a fifth of a second is finer than any state lasts.
EVERY = timedelta(seconds=0.2)


class GroupStates:
    """Follows every group of the signal plan, and says when its state changes."""

    def __init__(self, config: SiteConfig, config_hash: str, signals: Signals) -> None:
        self._camera = config.camera.id
        self._config_hash = config_hash
        self._signals = signals
        self._groups = list(config.signal_plan.groups) if config.signal_plan else []
        # For each group and stream: the state last told, and the time it was told for.
        self._told: dict[tuple[str, bool], tuple[SignalState, SignalSource | None, datetime]] = {}
        self._asked_at: datetime | None = None

    def update(self, timestamp: datetime) -> list[GroupState]:
        if self._asked_at is not None and timestamp - self._asked_at < EVERY:
            return []
        self._asked_at = timestamp
        changes = []
        for group in self._groups:
            for settled, at in ((False, timestamp), (True, timestamp - SETTLE)):
                line = self._signals.group_state(group, at)
                told = self._told.get((group, settled))
                if told is not None and told[:2] == (line.state, line.source):
                    continue
                # Dated from when the state began, but never before what was last told.
                since = at if line.since is None else min(line.since, at)
                ts = since if told is None else max(since, told[2])
                self._told[(group, settled)] = (line.state, line.source, ts)
                changes.append(self._record(group, line.state, line.source, settled, ts))
        return changes

    def _record(
        self,
        group: str,
        state: SignalState,
        source: SignalSource | None,
        settled: bool,
        ts: datetime,
    ) -> GroupState:
        stream = "settled" if settled else "live"
        name = f"trafficcam/{self._camera}/groups/{group}/{stream}/{ts.isoformat()}/{state.value}"
        return GroupState.model_validate(
            {
                # Derived from what it says, so a replay gives the same ids.
                "id": uuid.uuid5(uuid.NAMESPACE_URL, name),
                "ts": ts,
                "camera": self._camera,
                "config_hash": self._config_hash,
                "group": group,
                "state": state,
                "source": source,
                "settled": settled,
            }
        )
