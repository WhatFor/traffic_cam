"""Mean colour of small regions of a full-resolution frame."""

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt

from trafficcam.sources import Rgb

Rect = tuple[int, int, int, int]  # x, y, w, h


def sample_rgb(image: npt.NDArray[np.uint8], regions: Mapping[str, Rect]) -> dict[str, Rgb]:
    """Sample an RGB image, height x width x 3."""
    samples = {}
    for name, (x, y, w, h) in regions.items():
        r, g, b = image[y : y + h, x : x + w].reshape(-1, 3).mean(axis=0)
        samples[name] = (float(r), float(g), float(b))
    return samples


def sample_yuv420(
    buffer: npt.NDArray[np.uint8],
    size: tuple[int, int],
    stride: int,
    regions: Mapping[str, Rect],
) -> dict[str, Rgb]:
    """Sample a planar YUV 4:2:0 buffer without converting the whole frame.

    `buffer` is flat: `height` rows of `stride` luma bytes, then the U plane and the V
    plane, each `height / 2` rows of `stride / 2` bytes. Colours are Rec. 709, limited range,
    which is what the camera's video configuration produces.
    """
    _, height = size
    chroma_stride, chroma_height = stride // 2, height // 2
    luma_end = height * stride
    chroma_size = chroma_height * chroma_stride
    luma = buffer[:luma_end].reshape(height, stride)
    u_plane = buffer[luma_end : luma_end + chroma_size].reshape(chroma_height, chroma_stride)
    v_plane = buffer[luma_end + chroma_size : luma_end + 2 * chroma_size].reshape(
        chroma_height, chroma_stride
    )
    samples = {}
    for name, (x, y, w, h) in regions.items():
        rows = slice(y // 2, (y + h + 1) // 2)
        columns = slice(x // 2, (x + w + 1) // 2)
        samples[name] = _to_rgb(
            float(luma[y : y + h, x : x + w].mean()),
            float(u_plane[rows, columns].mean()),
            float(v_plane[rows, columns].mean()),
        )
    return samples


def _to_rgb(y: float, u: float, v: float) -> Rgb:
    luma, blue, red = 1.164 * (y - 16), u - 128, v - 128
    return (
        min(max(luma + 1.793 * red, 0.0), 255.0),
        min(max(luma - 0.213 * blue - 0.533 * red, 0.0), 255.0),
        min(max(luma + 2.112 * blue, 0.0), 255.0),
    )
