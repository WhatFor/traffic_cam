"""Parsing of the NMS output of Hailo's YOLOv8 models."""

from collections.abc import Collection, Sequence

import numpy as np
import numpy.typing as npt

Parsed = tuple[npt.NDArray[np.float32], npt.NDArray[np.float32], npt.NDArray[np.int_]]


def parse_nms_output(
    output: Sequence[npt.NDArray[np.float32]], class_ids: Collection[int], threshold: float
) -> Parsed:
    """Return boxes (x0, y0, x1, y1, normalised), confidences and class ids.

    `output` has one array per COCO class, with rows of y0, x0, y1, x1, score.
    """
    boxes, confidences, classes = [], [], []
    for class_id in class_ids:
        rows = output[class_id]
        rows = rows[rows[:, 4] >= threshold]
        boxes.append(rows[:, [1, 0, 3, 2]])
        confidences.append(rows[:, 4])
        classes.append(np.full(len(rows), class_id, dtype=np.int_))
    if not boxes:
        return np.empty((0, 4), np.float32), np.empty(0, np.float32), np.empty(0, np.int_)
    return np.concatenate(boxes), np.concatenate(confidences), np.concatenate(classes)
