"""ByteTracker confirms tracks, keeps their ids and never returns unconfirmed ones."""

from datetime import UTC, datetime, timedelta

import numpy as np
import supervision as sv

from trafficcam.config import Tracking
from trafficcam.tracking.bytetrack import ByteTracker

FPS = 15
START = datetime(2026, 10, 4, 13, 5, tzinfo=UTC)
TRACKING = Tracking(
    lost_s=2.0,
    activation_threshold=0.5,
    high_confidence_threshold=0.5,
    min_consecutive_frames=3,
    min_iou=0.1,
)


def at(frame: int) -> datetime:
    return START + timedelta(seconds=frame / FPS)


def boxes(*lefts: float, confidence: float = 0.9) -> sv.Detections:
    """One 100x60 box per left edge, all on the same row."""
    return sv.Detections(
        xyxy=np.array([[x, 500, x + 100, 560] for x in lefts], dtype=np.float32).reshape(-1, 4),
        confidence=np.full(len(lefts), confidence, dtype=np.float32),
        class_id=np.full(len(lefts), 2),
    )


def ids(tracks: sv.Detections) -> list[int]:
    return [] if tracks.tracker_id is None else sorted(tracks.tracker_id.tolist())


def run(tracker: ByteTracker, frames: range, *starts: float, speed: float = 5) -> list[list[int]]:
    """Feed boxes that begin at `starts` and move right by `speed` pixels a frame."""
    return [
        ids(tracker.update(boxes(*(x + speed * frame for x in starts)), at(frame)))
        for frame in frames
    ]


def test_a_moving_box_is_confirmed_and_keeps_its_id() -> None:
    seen = run(ByteTracker(TRACKING, FPS), range(30), 100)

    assert seen[0] == []
    confirmed = [frame_ids for frame_ids in seen if frame_ids]
    assert len(confirmed) >= 30 - TRACKING.min_consecutive_frames
    assert len({tuple(frame_ids) for frame_ids in confirmed}) == 1
    assert all(track_id >= 0 for frame_ids in seen for track_id in frame_ids)


def test_a_box_seen_too_briefly_yields_no_track() -> None:
    seen = run(ByteTracker(TRACKING, FPS), range(TRACKING.min_consecutive_frames - 1), 100)

    assert seen == [[]] * (TRACKING.min_consecutive_frames - 1)


def test_two_separate_boxes_get_different_ids() -> None:
    seen = run(ByteTracker(TRACKING, FPS), range(15), 100, 900)

    assert len(seen[-1]) == 2
    assert seen[-1][0] != seen[-1][1]


def test_a_short_gap_keeps_the_id_and_a_long_gap_does_not() -> None:
    tracker = ByteTracker(TRACKING, FPS)
    (before,) = run(tracker, range(15), 100)[-1]

    short_gap = range(15 + FPS, 30 + FPS)
    for frame in range(15, short_gap.start):
        tracker.update(boxes(), at(frame))
    (after_short,) = run(tracker, short_gap, 100)[-1]

    long_gap = range(short_gap.stop + 4 * FPS, short_gap.stop + 5 * FPS)
    for frame in range(short_gap.stop, long_gap.start):
        tracker.update(boxes(), at(frame))
    (after_long,) = run(tracker, long_gap, 100)[-1]

    assert after_short == before
    assert after_long != before


def test_a_low_confidence_detection_does_not_start_a_track() -> None:
    tracker = ByteTracker(TRACKING, FPS)

    seen = [ids(tracker.update(boxes(100, confidence=0.4), at(frame))) for frame in range(15)]

    assert seen == [[]] * 15
