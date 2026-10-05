"""Crop planner: cuts model inputs out of a frame and maps the results back."""

import numpy as np
import numpy.typing as npt
import supervision as sv
from PIL import Image

from trafficcam.config import Rect

Size = tuple[int, int]  # width, height


def cut_crop(
    image: npt.NDArray[np.uint8], crop: Rect, frame_size: Size, model_size: Size
) -> npt.NDArray[np.uint8]:
    """Cut `crop`, given in full-frame pixels, out of `image` and resize it for the model.

    `image` may be smaller than the frame, as the low-res stream is; the crop is scaled to match.
    """
    height, width = image.shape[:2]
    scale_x, scale_y = width / frame_size[0], height / frame_size[1]
    x, y, w, h = crop
    region = image[
        round(y * scale_y) : round((y + h) * scale_y),
        round(x * scale_x) : round((x + w) * scale_x),
    ]
    resized = Image.fromarray(region).resize(model_size, Image.Resampling.BILINEAR)
    # np.array, not np.asarray: the Hailo runtime rejects read-only buffers.
    return np.array(resized)


def boxes_to_frame(boxes: npt.NDArray[np.float32], crop: Rect) -> npt.NDArray[np.float32]:
    """Map boxes normalised to a crop (x0, y0, x1, y1 in 0..1) to full-frame pixels."""
    x, y, w, h = crop
    scale = np.array([w, h, w, h], dtype=np.float32)
    offset = np.array([x, y, x, y], dtype=np.float32)
    return boxes * scale + offset


def merge(per_crop: list[sv.Detections], iou: float) -> sv.Detections:
    """Combine detections from overlapping crops, keeping one box per object and class."""
    return sv.Detections.merge(per_crop).with_nms(threshold=iou)
