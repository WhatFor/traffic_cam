"""The preview draws the configured geometry onto a frame of the camera's size."""

from PIL import Image, ImageChops
from test_config import FULL

from trafficcam.config import SiteConfig
from trafficcam.preview import render


def test_render_draws_on_the_frame() -> None:
    config = SiteConfig.model_validate(FULL)
    blank = Image.new("RGB", config.camera.size)

    preview = render(blank, config)

    assert preview.size == blank.size
    assert ImageChops.difference(preview, blank).getbbox() is not None
