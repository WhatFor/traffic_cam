"""The site config model accepts the repo's config and the plan's shapes, and rejects mistakes."""

import copy
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from trafficcam.config import ConfigError, load_site_config

SITE_YAML = Path(__file__).parents[2] / "config" / "site.yaml"

# One of everything, in the shapes the build plan gives.
FULL: dict[str, Any] = {
    "camera": {"id": "junction-1", "size": [2028, 1520], "fps": 15},
    "inference": {
        "model": "/usr/share/hailo-models/yolov8s_h8.hef",
        "threshold": 0.3,
        "classes": ["car", "truck", "bus", "motorcycle", "bicycle", "person"],
        "crops": [[600, 500, 700, 700]],
    },
    "zones": {
        "approach_south": {"polygon": [[900, 1100], [1300, 1100], [1300, 1500], [900, 1500]]},
        "exit_east": {"polygon": [[1500, 600], [2000, 600], [2000, 900]]},
    },
    "lines": {"stopline_south": {"points": [[900, 1100], [1300, 1100]], "direction": "inbound"}},
    "movements": {"left_turn_watch": {"from": "approach_south", "to": "exit_east"}},
    "signal_heads": {
        "sh_south_primary": {
            "lamps": {
                "red": [640, 520, 8, 8],
                "amber": [640, 530, 8, 8],
                "green": [640, 540, 8, 8],
            },
            "controls": ["stopline_south"],
        }
    },
    "detectors": {
        "box_junction": {"min_stationary_s": 3.0, "exempt_movements": ["left_turn_watch"]},
        "red_light": {"grace_s": 0.5},
        "speed": {"homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "limit_mph": 30},
        "incident": {"decel_mps2": 6.0, "notify_min_confidence": 0.7},
    },
    "clips": {
        "dir": "/mnt/data/clips",
        "pre_s": 5,
        "post_s": 55,
        "retention_days": 30,
        "max_gb": 200,
    },
}


def load(tmp_path: Path, data: dict[str, Any]) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data))
    load_site_config(path)


def test_repo_config_loads() -> None:
    config, config_hash = load_site_config(SITE_YAML)

    assert config.camera.size == (2028, 1520)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", config_hash)
    assert config_hash == "sha256:" + hashlib.sha256(SITE_YAML.read_bytes()).hexdigest()


def test_hash_changes_with_the_file(tmp_path: Path) -> None:
    edited = tmp_path / "site.yaml"
    edited.write_bytes(SITE_YAML.read_bytes() + b"# edited\n")

    assert load_site_config(edited)[1] != load_site_config(SITE_YAML)[1]


def test_full_config_is_accepted(tmp_path: Path) -> None:
    load(tmp_path, FULL)


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        for key in parents:
            data = data[key]
        data[last] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_set("camera.fpss", 15), "fpss"),
        (_set("inference.threshold", 1.5), "threshold"),
        (_set("inference.classes", ["van"]), "classes"),
        (_set("inference.crops", [[600, 500, 700, 600]]), "not square"),
        (_set("inference.crops", [[1500, 500, 700, 700]]), "outside the frame"),
        (_set("zones.exit_east.polygon", [[1500, 600], [2000, 600]]), "polygon"),
        (_set("zones.exit_east.polygon", [[1500, 600], [2100, 600], [2000, 900]]), "outside"),
        (_set("lines.stopline_south.direction", "sideways"), "direction"),
        (_set("movements.left_turn_watch.to", "exit_west"), "unknown zone 'exit_west'"),
        (_set("signal_heads.sh_south_primary.controls", ["stopline_north"]), "stopline_north"),
        (_set("detectors.box_junction.exempt_movements", ["right_turn"]), "right_turn"),
    ],
)
def test_mistakes_are_rejected(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    data = copy.deepcopy(FULL)
    mutate(data)

    with pytest.raises(ConfigError, match=message):
        load(tmp_path, data)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_site_config(tmp_path / "absent.yaml")
