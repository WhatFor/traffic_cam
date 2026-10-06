"""Writes track logs without ever holding up the frame loop."""

import contextlib
import queue
import re
import sys
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

import supervision as sv

from trafficcam.contracts import SignalState
from trafficcam.geometry import Observation
from trafficcam.pipeline import FrameResult
from trafficcam.tracklog import SCHEMA, Header, to_record

HOUR_FORMAT = "%Y-%m-%dT%H"
FILE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}\.jsonl")
RETENTION = timedelta(days=7)
MAX_QUEUED = 900  # a minute of frames
LOG_EVERY_DROPPED = 900
FLUSH_INTERVAL_S = 1.0
RETRY_S = 60.0
CLOSE_TIMEOUT_S = 2.0

_Item = tuple[int, datetime, sv.Detections, Observation, Mapping[str, SignalState]]


class TrackLogWriter:
    """Queues each frame's result; a thread writes them to one file per UTC hour.

    The directory must already exist. While it is missing or a write fails, records are
    dropped and counted, and the writer tries again every `retry_s`. Files older than the
    retention period are deleted when a new hour's file is opened.

    A full queue drops frames too, unless `wait_when_full` is set: a replay can wait for
    the disk, the live camera cannot.
    """

    def __init__(
        self,
        directory: Path,
        *,
        camera: str,
        config_hash: str,
        wait_when_full: bool = False,
        max_queued: int = MAX_QUEUED,
        retry_s: float = RETRY_S,
    ) -> None:
        self._directory = directory
        self._camera = camera
        self._config_hash = config_hash
        self._wait_when_full = wait_when_full
        self._retry_s = retry_s
        self._queue: queue.Queue[_Item] = queue.Queue(max_queued)
        self._stopping = threading.Event()
        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self._file: TextIO | None = None
        self._hour: str | None = None
        self._flush_at = 0.0
        self._retry_at = 0.0
        self._failing = False
        self._thread = threading.Thread(target=self._run, name="tracklog", daemon=True)
        self._thread.start()

    @property
    def dropped(self) -> int:
        return self._dropped

    def observe(self, result: FrameResult) -> None:
        frame = result.frame
        try:
            # Not the frame itself: a queue of images would be gigabytes.
            self._queue.put(
                (
                    frame.index,
                    frame.timestamp,
                    result.detections,
                    result.observation,
                    result.signals,
                ),
                block=self._wait_when_full,
            )
        except queue.Full:
            self._drop("its queue is full")

    def close(self) -> None:
        self._stopping.set()
        self._thread.join(CLOSE_TIMEOUT_S)

    def _drop(self, reason: str) -> None:
        with self._dropped_lock:
            self._dropped += 1
            dropped = self._dropped
        if dropped == 1 or dropped % LOG_EVERY_DROPPED == 0:
            print(f"track log: {reason}; {dropped} frames dropped", file=sys.stderr, flush=True)

    def _run(self) -> None:
        while not (self._stopping.is_set() and self._queue.empty()):
            try:
                item = self._queue.get(timeout=FLUSH_INTERVAL_S)
            except queue.Empty:
                continue
            if time.monotonic() < self._retry_at:
                self._drop("not writing")
                continue
            try:
                self._write(*item)
            except OSError as error:
                self._fail(error)
        self._close_file()

    def _write(
        self,
        index: int,
        timestamp: datetime,
        detections: sv.Detections,
        observation: Observation,
        signals: Mapping[str, SignalState],
    ) -> None:
        hour = timestamp.astimezone(UTC).strftime(HOUR_FORMAT)
        if hour != self._hour:
            self._open(hour, timestamp)
        assert self._file is not None
        record = to_record(index, timestamp, detections, observation, signals)
        self._file.write(record.model_dump_json(by_alias=True) + "\n")
        if time.monotonic() >= self._flush_at:
            self._file.flush()
            self._flush_at = time.monotonic() + FLUSH_INTERVAL_S

    def _open(self, hour: str, started: datetime) -> None:
        self._close_file()
        # Appending: a restart within the hour continues the same file under a new header.
        self._file = (self._directory / f"{hour}.jsonl").open("a", encoding="utf-8")
        self._hour = hour
        header = Header.model_validate(
            {
                "schema": SCHEMA,
                "camera": self._camera,
                "config_hash": self._config_hash,
                "started": started,
            }
        )
        self._file.write(header.model_dump_json(by_alias=True) + "\n")
        if self._failing:
            self._failing = False
            print("track log: writing again", file=sys.stderr, flush=True)
        self._prune(hour)

    def _prune(self, hour: str) -> None:
        oldest = (datetime.strptime(hour, HOUR_FORMAT) - RETENTION).strftime(HOUR_FORMAT)
        for path in self._directory.iterdir():
            if FILE_NAME.fullmatch(path.name) and path.stem < oldest:
                path.unlink()

    def _fail(self, error: OSError) -> None:
        self._close_file()
        self._retry_at = time.monotonic() + self._retry_s
        if not self._failing:
            self._failing = True
            print(
                f"track log: {error}; trying again every {self._retry_s:.0f} s",
                file=sys.stderr,
                flush=True,
            )
        self._drop("not writing")

    def _close_file(self) -> None:
        self._hour = None
        if self._file is not None:
            with contextlib.suppress(OSError):
                self._file.close()
            self._file = None
