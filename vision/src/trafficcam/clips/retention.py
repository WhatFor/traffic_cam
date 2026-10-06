"""Clip retention: files are deleted by age, then oldest first to stay under a size."""

import contextlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from trafficcam.clips.writer import PART

# <dir>/YYYY/MM/DD/<clip id>.mp4, with a .jpg and a .json of the same name beside it.
DAY_GLOB = "[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"
CLIP_NAME = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\.mp4")
BESIDE = (".jpg", ".json")


@dataclass(frozen=True, slots=True)
class Pruned:
    deleted: list[uuid.UUID]
    remaining_bytes: int


def day_directory(root: Path, at: datetime) -> Path:
    return root / f"{at:%Y/%m/%d}"


def prune(
    root: Path, now: datetime, retention: timedelta, max_bytes: float, *, unfinished: bool = False
) -> Pruned:
    """Delete clips older than `retention`, then the oldest until the rest fit in `max_bytes`.

    Only files named as clips are touched. `unfinished` also removes part-written files,
    which is right only when nothing is being written.
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
                clips.append((path.stat().st_mtime, sum(f.stat().st_size for f in files), files))
    clips.sort(key=lambda clip: clip[0])
    total = sum(size for _, size, _ in clips)

    deleted = []
    oldest_kept = (now - retention).timestamp()
    for modified, size, files in clips:
        if modified >= oldest_kept and total <= max_bytes:
            break
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
