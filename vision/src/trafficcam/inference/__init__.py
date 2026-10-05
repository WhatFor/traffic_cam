"""Object detection: backends that turn a frame into detections."""

from typing import Protocol

import supervision as sv

from trafficcam.contracts import RoadUserClass
from trafficcam.sources import Frame

COCO_CLASS_IDS: dict[RoadUserClass, int] = {
    RoadUserClass.person: 0,
    RoadUserClass.bicycle: 1,
    RoadUserClass.car: 2,
    RoadUserClass.motorcycle: 3,
    RoadUserClass.bus: 5,
    RoadUserClass.truck: 7,
}


class InferenceBackend(Protocol):
    def detect(self, frame: Frame) -> sv.Detections:
        """Detections with `xyxy` in full-frame pixels and COCO ids as `class_id`."""
        ...
