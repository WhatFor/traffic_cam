"""ByteTrack, from the `trackers` package."""

from datetime import datetime
from typing import cast

import supervision as sv
from trackers import ByteTrackTracker

from trafficcam.config import Tracking

UNCONFIRMED = -1


class ByteTracker:
    def __init__(self, tracking: Tracking, fps: int) -> None:
        self._tracker = ByteTrackTracker(
            # The library counts this buffer in frames at 30 fps, whatever the real rate.
            lost_track_buffer=round(tracking.lost_s * 30),
            frame_rate=fps,
            track_activation_threshold=tracking.activation_threshold,
            high_conf_det_threshold=tracking.high_confidence_threshold,
            minimum_consecutive_frames=tracking.min_consecutive_frames,
            minimum_iou_threshold=tracking.min_iou,
        )

    def update(self, detections: sv.Detections, timestamp: datetime) -> sv.Detections:
        tracked = self._tracker.update(detections, timestamp=timestamp.timestamp())
        if tracked.tracker_id is None:
            return sv.Detections.empty()
        # Unconfirmed tracks all share one id, which zone and line logic would read as one object.
        return cast(sv.Detections, tracked[tracked.tracker_id != UNCONFIRMED])
