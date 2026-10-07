"""Writes the clips that triggers ask for, from the ring, without holding up anything else."""

import queue
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from av.error import FFmpegError

from trafficcam.clips import ClipRecord, PacketRing
from trafficcam.clips.plan import ClipPlan, PlannedClip
from trafficcam.clips.retention import day_directory, mark_kept, prune
from trafficcam.clips.writer import ClipFile, VideoStream, save_still
from trafficcam.config import Clips
from trafficcam.contracts import Clip, ClipCommand, ClipDeleted, ClipKeep, ClipTrigger, Event

MANUAL = "manual"
POLL_S = 0.25
PRUNE_EVERY = timedelta(days=1)
CLOSE_TIMEOUT_S = 10.0
GIGABYTE = 1_000_000_000
# What can go wrong writing or reading a clip: the disk, the container, or its contents.
_ERRORS = (OSError, FFmpegError, ValueError)


class RingBufferRecorder:
    """Cuts clips out of the ring of recent video.

    Triggers only note what is wanted and return. A thread does the rest: it copies packets
    from the ring into each open clip until the clip's end has passed, then saves a still
    frame and the clip's record beside it, and applies retention. Finished clips and
    deletions wait in a queue for `drain`. The thread also marks the clips it is told to keep.

    The directory must already exist. A clip that cannot be written is dropped and counted.
    """

    def __init__(
        self,
        ring: PacketRing,
        settings: Clips,
        stream: VideoStream,
        *,
        camera: str,
        config_hash: str,
        run_thread: bool = True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ring = ring
        self._settings = settings
        self._stream = stream
        self._camera = camera
        self._config_hash = config_hash
        self._now = now
        self._plan = ClipPlan(settings.max_s)
        self._lock = threading.Lock()
        self._files: dict[uuid.UUID, ClipFile] = {}
        self._cursors: dict[uuid.UUID, int] = {}
        self._records: queue.SimpleQueue[ClipRecord] = queue.SimpleQueue()
        self._keeps: queue.SimpleQueue[ClipKeep] = queue.SimpleQueue()
        self._prune_at: datetime | None = None
        self.written = 0
        self.failed = 0
        self.folder_bytes = 0
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="clips", daemon=True)
        if run_thread:
            self._thread.start()

    def for_event(self, event: Event) -> Event:
        if not self._settings.wants(event.type, event.attrs):
            return event
        trigger = ClipTrigger(type=event.type, at=event.ts, event_id=event.id, reason=None)
        clip_id = self._trigger(trigger, *self._settings.lengths(event.type))
        return event.model_copy(update={"clip_id": clip_id})

    def command(self, command: ClipCommand) -> None:
        pre_s, post_s = self._settings.lengths()
        # No further back than the ring holds, and no longer than a clip may be.
        if command.pre_s is not None:
            pre_s = min(command.pre_s, self._settings.buffer_s, self._settings.max_s)
        if command.post_s is not None:
            post_s = max(0.0, min(command.post_s, self._settings.max_s - pre_s))
        # Its arrival, not the sender's clock, is the moment that was asked about.
        trigger = ClipTrigger(type=MANUAL, at=self._now(), event_id=None, reason=command.reason)
        self._trigger(trigger, pre_s, post_s)

    def keep(self, keep: ClipKeep) -> None:
        self._keeps.put(keep)

    def drain(self) -> list[ClipRecord]:
        records = []
        while not self._records.empty():
            records.append(self._records.get())
        return records

    def close(self) -> None:
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join(CLOSE_TIMEOUT_S)

    def step(self) -> None:
        """One pass over the open clips. The thread calls this; tests can too."""
        if self._prune_at is None:
            # Nothing is being written yet, so any part-written file is debris from last time.
            self._prune(unfinished=True)
        with self._lock:
            clips = list(self._plan.open)
        for clip in clips:
            self._guarded(clip, self._advance)
        self._mark_kept()
        if self._prune_at is not None and self._now() >= self._prune_at:
            self._prune()

    def finish_open(self) -> None:
        """Close every open clip with what it has: for shutdown."""
        with self._lock:
            clips = list(self._plan.open)
        for clip in clips:
            self._guarded(clip, lambda clip: self._advance(clip, cut_short=True))

    def _trigger(self, trigger: ClipTrigger, pre_s: float, post_s: float) -> uuid.UUID:
        with self._lock:
            return self._plan.add(trigger, pre_s, post_s).id

    def _mark_kept(self) -> None:
        while not self._keeps.empty():
            keep = self._keeps.get()
            try:
                mark_kept(self._settings.dir, keep.id, keep.keep)
            except OSError as error:
                print(f"clip {keep.id}: not marked: {error}", file=sys.stderr, flush=True)

    def _run(self) -> None:
        while not self._stopping.wait(POLL_S):
            self.step()
        self.finish_open()

    def _guarded(self, clip: PlannedClip, action: Callable[[PlannedClip], None]) -> None:
        try:
            action(clip)
        except _ERRORS as error:
            self._fail(clip, error)

    def _advance(self, clip: PlannedClip, cut_short: bool = False) -> None:
        file = self._files.get(clip.id)
        if file is None:
            start_pts = self._ring.pts_of(clip.start)
            packets = [] if start_pts is None else self._ring.from_keyframe(start_pts)
        else:
            packets = self._ring.after(self._cursors[clip.id])
            if packets and packets[0].seq != self._cursors[clip.id] + 1:
                raise ValueError("the video left the ring before it was written")

        with self._lock:
            end_pts = self._ring.pts_of(clip.end) if packets else None
            wanted = [packet for packet in packets if end_pts is None or packet.pts_us <= end_pts]
            done = cut_short or len(wanted) < len(packets)
            if done:
                # From here on no trigger can extend it.
                self._plan.close(clip)

        if file is None and wanted:
            file = self._files[clip.id] = self._open(clip, wanted[0].pts_us)
        if file is not None:
            file.write(wanted)
        if packets:
            self._cursors[clip.id] = packets[-1].seq
        if not done:
            return
        if file is None:
            raise ValueError("there was no video for it in the ring")
        self._finish(clip, file)

    def _open(self, clip: PlannedClip, first_pts: int) -> ClipFile:
        root = self._settings.dir
        if not root.is_dir():
            raise FileNotFoundError(f"{root} is not there")
        directory = day_directory(root, self._ring.time_of(first_pts))
        directory.mkdir(parents=True, exist_ok=True)
        return ClipFile(directory / f"{clip.id}.mp4", self._stream)

    def _finish(self, clip: PlannedClip, file: ClipFile) -> None:
        if file.first_pts is None or file.last_pts is None:
            raise ValueError("it has no video")
        file.close()
        del self._files[clip.id]
        self._cursors.pop(clip.id, None)
        started, ended = self._ring.time_of(file.first_pts), self._ring.time_of(file.last_pts)
        first = clip.triggers[0]

        still = file.path.with_suffix(".jpg")
        offset_s = (min(max(first.at, started), ended) - started).total_seconds()
        try:
            save_still(file.path, offset_s, still)
        except _ERRORS as error:
            print(f"clip {clip.id}: no still frame: {error}", file=sys.stderr, flush=True)
            still = None

        record = Clip.model_validate(
            {
                "id": clip.id,
                "ts": self._now(),
                "camera": self._camera,
                "config_hash": self._config_hash,
                "event_id": first.event_id,
                "triggers": clip.triggers,
                "path": str(file.path),
                "keyframe_path": None if still is None else str(still),
                "started_at": started,
                "ended_at": ended,
                "bytes": file.path.stat().st_size,
            }
        )
        # Beside the clip, so a copy of the file still says what it shows.
        file.path.with_suffix(".json").write_text(
            record.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8"
        )
        self.written += 1
        self._records.put(record)
        self._prune()

    def _fail(self, clip: PlannedClip, error: Exception) -> None:
        with self._lock:
            if clip in self._plan.open:
                self._plan.close(clip)
        file = self._files.pop(clip.id, None)
        self._cursors.pop(clip.id, None)
        if file is not None:
            file.abandon()
        self.failed += 1
        print(f"clip {clip.id} not written: {error}", file=sys.stderr, flush=True)

    def _prune(self, unfinished: bool = False) -> None:
        now = self._now()
        self._prune_at = now + PRUNE_EVERY
        try:
            pruned = prune(
                self._settings.dir,
                now,
                timedelta(days=self._settings.retention_days),
                self._settings.max_gb * GIGABYTE,
                kept_for=timedelta(days=self._settings.kept_days),
                unfinished=unfinished,
            )
        except OSError as error:
            print(f"clip retention: {error}", file=sys.stderr, flush=True)
            return
        self.folder_bytes = pruned.remaining_bytes
        for clip_id in pruned.deleted:
            self._records.put(
                ClipDeleted.model_validate({"id": clip_id, "ts": now, "camera": self._camera})
            )
