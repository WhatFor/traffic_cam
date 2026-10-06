"""The calibration tool turns map coordinates into metres and checks the fit."""

from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image
from test_groundmap import MEASURED, pixels_of

from trafficcam import calibrate
from trafficcam.calibrate import locate, metres_from, site_yaml_block
from trafficcam.config import Speed

ORIGIN = (53.5, -2.25)


def on_the_map(ground: np.ndarray) -> list[tuple[float, float]]:
    """A latitude and longitude for each position in metres east and north of ORIGIN."""
    east_per_degree, north_per_degree = metres_from(ORIGIN, ORIGIN[0] + 1e-9, ORIGIN[1] + 1e-9)
    return [
        (ORIGIN[0] + north * 1e-9 / north_per_degree, ORIGIN[1] + east * 1e-9 / east_per_degree)
        for east, north in ground.tolist()
    ]


def points_file(tmp_path: Path) -> Path:
    points = {}
    places = zip(pixels_of(MEASURED).tolist(), on_the_map(MEASURED), strict=True)
    for index, (pixel, (lat, lon)) in enumerate(places):
        points[f"p{index}"] = {"pixel": pixel, "lat": lat, "lon": lon}
    ((lat, lon),) = on_the_map(np.array([[80.0, 20.0]]))
    points["hidden"] = {"pixel": None, "lat": lat, "lon": lon}
    points["not_yet_found"] = {"pixel": [100, 100], "lat": None, "lon": None}
    path = tmp_path / "ground_points.yaml"
    path.write_text(yaml.safe_dump({"points": points}, sort_keys=False))
    return path


def test_degrees_become_metres_at_this_latitude() -> None:
    east, north = metres_from(ORIGIN, ORIGIN[0] + 0.001, ORIGIN[1] + 0.001)

    # A thousandth of a degree is about 111 m northwards anywhere, and less eastwards
    # the further from the equator.
    assert north == pytest.approx(111.3, abs=0.1)
    assert east == pytest.approx(66.4, abs=0.1)
    assert metres_from(ORIGIN, *ORIGIN) == (0.0, 0.0)


def test_points_are_located_from_the_first_one_on_the_map(tmp_path: Path) -> None:
    located = locate(calibrate.load(points_file(tmp_path)))

    assert [point.name for point in located] == [*(f"p{index}" for index in range(6)), "hidden"]
    assert located[0].ground == (0.0, 0.0)
    assert located[2].ground == pytest.approx((60.0, 40.0), abs=0.01)
    assert located[-1].pixel is None


def test_the_block_for_site_yaml_holds_metres_and_no_coordinates(tmp_path: Path) -> None:
    block = site_yaml_block(locate(calibrate.load(points_file(tmp_path))))

    settings = yaml.safe_load(block) | {"limit_mph": 30, "flag_above_mph": 35}
    assert len(Speed.model_validate(settings).ground_points) == 6
    assert "hidden" not in block
    assert "53." not in block.replace("pixel", "")


def test_fit_reports_the_errors_and_writes_the_check_images(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = Image.new("RGB", (2028, 1520), (90, 90, 90))

    calibrate.fit(calibrate.load(points_file(tmp_path)), frame, tmp_path / "out")
    printed = capsys.readouterr().out

    assert "root mean square               0.00" in printed
    assert "hidden is hidden from the camera" in printed
    assert "ground_points:" in printed
    with Image.open(tmp_path / "out" / "ground_grid.png") as grid:
        assert grid.size == frame.size
    with Image.open(tmp_path / "out" / "ground_plan.png") as plan:
        assert plan.size[0] > plan.size[1]


def test_points_are_drawn_where_the_camera_sees_them(tmp_path: Path) -> None:
    frame = Image.new("RGB", (2028, 1520), (90, 90, 90))

    marked = calibrate.draw_points(frame, calibrate.load(points_file(tmp_path)))

    assert marked.size == frame.size
    assert marked.getpixel((100, 108)) != (90, 90, 90)
    assert marked.getpixel((1900, 1400)) == (90, 90, 90)
