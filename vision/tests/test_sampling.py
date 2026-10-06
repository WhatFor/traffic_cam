"""Sampling small regions of a full-resolution frame, in RGB and in planar YUV."""

import numpy as np
import pytest

from trafficcam.sources.sampling import sample_rgb, sample_yuv420

WIDTH, HEIGHT, STRIDE = 40, 24, 48  # a stride wider than the picture, as the camera's is
PATCHES = {
    "red": ((4, 4, 6, 6), (200, 20, 40)),
    "amber": ((20, 4, 6, 6), (220, 150, 60)),
    "green": ((4, 14, 6, 6), (90, 200, 160)),
    "grey": ((20, 14, 6, 6), (128, 128, 128)),
}


def picture() -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), 30, dtype=np.uint8)
    for (x, y, w, h), colour in PATCHES.values():
        image[y : y + h, x : x + w] = colour
    return image


def to_yuv420(image: np.ndarray) -> np.ndarray:
    """Rec. 709 limited-range planar YUV 4:2:0, laid out as the camera's buffer is."""
    r, g, b = (image[..., i].astype(np.float64) for i in range(3))
    y = 16 + 0.1826 * r + 0.6142 * g + 0.0620 * b
    u = 128 - 0.1006 * r - 0.3386 * g + 0.4392 * b
    v = 128 + 0.4392 * r - 0.3989 * g - 0.0403 * b

    def padded(plane: np.ndarray, stride: int) -> np.ndarray:
        return np.pad(plane, ((0, 0), (0, stride - plane.shape[1])))

    planes = [padded(y, STRIDE)]
    for chroma in (u, v):
        half = chroma.reshape(HEIGHT // 2, 2, WIDTH // 2, 2).mean(axis=(1, 3))
        planes.append(padded(half, STRIDE // 2))
    return np.concatenate([plane.ravel() for plane in planes]).round().astype(np.uint8)


def test_rgb_samples_are_the_mean_of_each_region() -> None:
    regions = {name: rect for name, (rect, _) in PATCHES.items()}

    samples = sample_rgb(picture(), regions)

    for name, (_, colour) in PATCHES.items():
        assert samples[name] == pytest.approx(colour)


def test_yuv_samples_come_back_as_the_same_colours() -> None:
    regions = {name: rect for name, (rect, _) in PATCHES.items()}

    samples = sample_yuv420(to_yuv420(picture()), (WIDTH, HEIGHT), STRIDE, regions)

    for name, (_, colour) in PATCHES.items():
        assert samples[name] == pytest.approx(colour, abs=4)


def test_a_yuv_region_reads_only_its_own_pixels() -> None:
    samples = sample_yuv420(
        to_yuv420(picture()), (WIDTH, HEIGHT), STRIDE, {"background": (30, 4, 4, 4)}
    )

    assert samples["background"] == pytest.approx((30, 30, 30), abs=4)
