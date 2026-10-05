"""The crop planner and the Hailo output parser, which run without the hardware."""

import numpy as np
import pytest
import supervision as sv

from trafficcam.inference.crops import boxes_to_frame, cut_crop, merge
from trafficcam.inference.hailo_output import parse_nms_output

CAR, BUS = 2, 5


def detections(
    boxes: list[list[float]], confidence: list[float], class_id: list[int]
) -> sv.Detections:
    return sv.Detections(
        xyxy=np.array(boxes, dtype=np.float32).reshape(-1, 4),
        confidence=np.array(confidence, dtype=np.float32),
        class_id=np.array(class_id, dtype=int),
    )


def test_cut_crop_scales_the_rectangle_to_a_smaller_image() -> None:
    # A half-size image of a 200x100 frame, white inside the crop's frame rectangle.
    image = np.zeros((50, 100, 3), dtype=np.uint8)
    image[10:30, 20:40] = 255

    tensor = cut_crop(image, (40, 20, 40, 40), frame_size=(200, 100), model_size=(64, 64))

    assert tensor.shape == (64, 64, 3)
    assert tensor.dtype == np.uint8
    assert tensor.flags.writeable
    assert tensor.min() == 255


def test_boxes_to_frame_offsets_and_scales() -> None:
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.25, 0.5, 0.75, 1.0]], dtype=np.float32)

    mapped = boxes_to_frame(boxes, (480, 340, 1060, 1060))

    assert mapped.tolist() == [[480, 340, 1540, 1400], [745, 870, 1275, 1400]]


def nms_output(rows_by_class: dict[int, list[list[float]]]) -> list[np.ndarray]:
    return [
        np.array(rows_by_class.get(class_id, []), dtype=np.float32).reshape(-1, 5)
        for class_id in range(80)
    ]


def test_parse_nms_output_keeps_wanted_classes_above_threshold() -> None:
    output = nms_output(
        {
            CAR: [[0.1, 0.2, 0.3, 0.4, 0.9], [0.5, 0.5, 0.6, 0.6, 0.25]],
            BUS: [[0.0, 0.0, 0.5, 0.5, 0.6]],
            9: [[0.0, 0.0, 0.1, 0.1, 0.95]],
        }
    )

    boxes, confidence, class_id = parse_nms_output(output, [CAR, BUS], threshold=0.3)

    # Rows arrive as y0, x0, y1, x1 and leave as x0, y0, x1, y1.
    assert np.allclose(boxes, [[0.2, 0.1, 0.4, 0.3], [0.0, 0.0, 0.5, 0.5]])
    assert np.allclose(confidence, [0.9, 0.6])
    assert class_id.tolist() == [CAR, BUS]


def test_parse_nms_output_of_nothing_is_empty() -> None:
    boxes, confidence, class_id = parse_nms_output(nms_output({}), [CAR, BUS], threshold=0.3)

    assert boxes.shape == (0, 4)
    assert len(confidence) == 0
    assert len(class_id) == 0


def test_merge_collapses_the_same_object_seen_in_two_crops() -> None:
    first = detections([[100, 100, 200, 200]], [0.9], [CAR])
    second = detections([[102, 101, 201, 199], [400, 400, 450, 450]], [0.7, 0.8], [CAR, CAR])

    merged = merge([first, second], iou=0.5)

    assert len(merged) == 2
    assert merged.confidence is not None
    assert sorted(merged.confidence.tolist()) == pytest.approx([0.8, 0.9])


def test_merge_keeps_overlapping_boxes_of_different_classes() -> None:
    merged = merge(
        [
            detections([[100, 100, 200, 200]], [0.9], [CAR]),
            detections([[100, 100, 200, 200]], [0.8], [BUS]),
        ],
        iou=0.5,
    )

    assert merged.class_id is not None
    assert sorted(merged.class_id.tolist()) == [CAR, BUS]


def test_merge_of_empty_crops_is_empty() -> None:
    assert len(merge([detections([], [], []), detections([], [], [])], iou=0.5)) == 0
