"""Clip retention: files are deleted by age, then oldest first to stay under a size.

A clip with a `.keep` file beside it is kept longer, and is never deleted for size.
"""

import contextlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from trafficcam.clips.writer import PART

# <dir>/YYYY/MM/DD/<clip id>.mp4, with a .jpg and a .json of the same name beside it,
# and an empty .keep if it is to be kept.
DAY_GLOB = "[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"
CLIP_NAME = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\.mp4")
KEEP = ".keep"
BESIDE = (".jpg", ".json", KEEP)


@dataclass(frozen=True, slots=True)
class Pruned:
    deleted: list[uuid.UUID]
    remaining_bytes: int


def day_directory(root: Path, at: datetime) -> Path:
    return root / f"{at:%Y/%m/%d}"


def mark_kept(root: Path, clip_id: uuid.UUID, keep: bool) -> bool:
    """Mark a clip to be kept, or not. False if the clip is not there."""
    found = next(root.glob(f"{DAY_GLOB}/{clip_id}.mp4"), None)
    if found is None:
        return False
    marker = found.with_suffix(KEEP)
    if keep:
        marker.touch()
    else:
        marker.unlink(missing_ok=True)
    return True


def prune(
    root: Path,
    now: datetime,
    retention: timedelta,
    max_bytes: float,
    *,
    kept_for: timedelta | None = None,
    unfinished: bool = False,
) -> Pruned:
    """Delete clips older than `retention`, then the oldest until the rest fit in `max_bytes`.

    A kept clip goes only when older than `kept_for`. It still counts towards the size, so
    the others make room for it. Only files named as clips are touched. `unfinished` also
    removes part-written files, which is right only when nothing is being written.
    """
    days = sorted(path for path in root.glob(DAY_GLOB) if path.is_dir())
    if unfinished:
        for day in days:
            for part in day.glob(f"*{PART}"):
                part.unlink()

    clips = []
    for day in days:
        for path in day.iterdir():
            if CLIP_NAME.fullmatch(path.name):
                files = [path, *(f for f in map(path.with_suffix, BESIDE) if f.exists())]
                kept = path.with_suffix(KEEP) in files
                clips.append(
                    (path.stat().st_mtime, sum(f.stat().st_size for f in files), files, kept)
                )
    clips.sort(key=lambda clip: clip[0])
    total = sum(size for _, size, _, _ in clips)

    deleted = []
    oldest = (now - retention).timestamp()
    oldest_kept = (now - (kept_for or retention)).timestamp()
    for modified, size, files, kept in clips:
        if kept and modified >= oldest_kept:
            continue
        if not kept and modified >= oldest and total <= max_bytes:
            continue
        for file in files:
            file.unlink()
        total -= size
        deleted.append(uuid.UUID(files[0].stem))

    for day in days:
        for directory in (day, day.parent, day.parent.parent):
            # Fails, and is meant to, while anything is left inside.
            with contextlib.suppress(OSError):
                directory.rmdir()
    return Pruned(deleted, total)
