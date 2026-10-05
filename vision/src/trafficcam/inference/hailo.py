"""Detection on the Hailo-8. Only importable on the Pi: the Hailo bindings come from apt."""

from types import TracebackType
from typing import Self

import supervision as sv
from picamera2.devices import Hailo  # pyright: ignore[reportMissingImports]

from trafficcam.config import Inference
from trafficcam.inference import COCO_CLASS_IDS
from trafficcam.inference.crops import Size, boxes_to_frame, cut_crop, merge
from trafficcam.inference.hailo_output import parse_nms_output
from trafficcam.sources import Frame


class HailoBackend:
    """Runs each configured crop through the model in turn. Use from one thread only."""

    def __init__(self, inference: Inference, frame_size: Size) -> None:
        self._inference = inference
        self._frame_size = frame_size
        self._class_ids = [COCO_CLASS_IDS[name] for name in inference.classes]
        self._hailo = Hailo(inference.model)
        height, width, _ = self._hailo.get_input_shape()
        self._model_size = (width, height)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._hailo.close()

    def detect(self, frame: Frame) -> sv.Detections:
        per_crop = []
        for crop in self._inference.crops:
            # Frames are RGB, which is what the model takes; BGR found fewer objects when tried.
            tensor = cut_crop(frame.image, crop, self._frame_size, self._model_size)
            boxes, confidence, class_id = parse_nms_output(
                self._hailo.run(tensor), self._class_ids, self._inference.threshold
            )
            per_crop.append(
                sv.Detections(
                    xyxy=boxes_to_frame(boxes, crop), confidence=confidence, class_id=class_id
                )
            )
        return merge(per_crop, self._inference.merge_iou)
