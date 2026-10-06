"""The site config model accepts the repo's config and the plan's shapes, and rejects mistakes."""

import copy
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from trafficcam.config import ConfigError, SiteConfig, Zone, load_site_config

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
    "tracking": {
        "lost_s": 2.0,
        "activation_threshold": 0.5,
        "high_confidence_threshold": 0.5,
        "min_consecutive_frames": 3,
        "min_iou": 0.1,
    },
    "junction": "box_junction",
    "zones": {
        "box_junction": {"polygon": [[900, 800], [1200, 600], [1500, 650], [1400, 900]]},
        "approach_south": {
            "role": "approach",
            "arm": "south",
            "polygon": [[900, 1100], [1300, 1100], [1300, 1500], [900, 1500]],
        },
        "exit_east": {
            "role": "exit",
            "arm": "east",
            "polygon": [[1500, 600], [2000, 600], [2000, 900]],
        },
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
        "speed": {
            # The image is the ground at 10 pixels to the metre, with north up.
            "ground_points": [
                {"pixel": [0, 0], "ground": [0, 100]},
                {"pixel": [2000, 0], "ground": [200, 100]},
                {"pixel": [2000, 1500], "ground": [200, -50]},
                {"pixel": [0, 1500], "ground": [0, -50]},
            ],
            "limit_mph": 30,
            "flag_above_mph": 35,
        },
        "incident": {"decel_mps2": 6.0, "notify_min_confidence": 0.7},
    },
    "clips": {
        "dir": "/mnt/data/clips",
        "pre_s": 5,
        "post_s": 55,
        "retention_days": 30,
        "max_gb": 200,
        "events": {
            "red_light": {"pre_s": 5, "post_s": 15},
            "incident_candidate": {},
            "speeding": {"min": {"speed_mph": 45}},
        },
    },
}


def load(tmp_path: Path, data: dict[str, Any]) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data))
    load_site_config(path)


def test_repo_config_loads() -> None:
    config, config_hash = load_site_config(SITE_YAML)

    assert config.camera.size == (2028, 1520)
    assert config.inference.merge_iou == 0.5
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
        (_set("tracking.lost_s", 0), "lost_s"),
        (_set("junction", "roundabout"), "unknown zone 'roundabout'"),
        (_set("zones.exit_east.arm", None), "needs an arm"),
        (_set("inference.classes", ["van"]), "classes"),
        (_set("inference.crops", [[600, 500, 700, 600]]), "not square"),
        (_set("inference.crops", [[1500, 500, 700, 700]]), "outside the frame"),
        (_set("zones.exit_east.polygon", [[1500, 600], [2000, 600]]), "polygon"),
        (_set("zones.exit_east.polygon", [[1500, 600], [2100, 600], [2000, 900]]), "outside"),
        (_set("lines.stopline_south.direction", "sideways"), "direction"),
        (_set("movements.left_turn_watch.to", "exit_west"), "unknown zone 'exit_west'"),
        (_set("signal_heads.sh_south_primary.controls", ["stopline_north"]), "stopline_north"),
        (_set("detectors.box_junction.exempt_movements", ["right_turn"]), "right_turn"),
        (_set("detectors.banned_turns", {"movements": ["no_such_turn"]}), "no_such_turn"),
        (
            _set(
                "detectors.banned_turns",
                {"movements": ["left_turn_watch"], "signal_heads": ["no_such_head"]},
            ),
            "no_such_head",
        ),
        (_set("signal_heads.sh_south_primary.lamps", {}), "lamps"),
        (_set("signal_heads.sh_south_primary.lamps.blue", [640, 550, 8, 8]), "lamps"),
        (_set("zones.exit_east.entry_heading", [0, 90]), "only for approach zones"),
        (_set("zones.approach_south.entry_heading", [0, 400]), "entry_heading"),
        (_set("clips.events.red_light.pre_s", 120), "pre_s must be less than buffer_s"),
        (_set("clips.max_s", 30), "more than max_s"),
        (_set("clips.events.red_light.length", 20), "length"),
        (_set("detectors.speed.flag_above_mph", 25), "below limit_mph"),
        (
            _set("detectors.speed.ground_points", [{"pixel": [0, 0], "ground": [0, 0]}] * 3),
            "ground_points",
        ),
        (
            _set(
                "detectors.speed.ground_points",
                [{"pixel": [x, x], "ground": [x, x]} for x in (0, 100, 200, 300)],
            ),
            "nearly in a line",
        ),
        (
            _set(
                "detectors.speed.ground_points",
                [
                    {"pixel": [0, 0], "ground": [0, 100]},
                    {"pixel": [2000, 0], "ground": [200, 100]},
                    {"pixel": [2000, 1500], "ground": [200, -50]},
                    {"pixel": [0, 1500], "ground": [0, -50]},
                    {"pixel": [1000, 750], "ground": [100, 30]},
                ],
            ),
            "point 5 is",
        ),
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


def test_an_entry_heading_range_can_wrap_past_zero() -> None:
    polygon = [(0, 0), (10, 0), (10, 10)]
    plain = Zone(polygon=polygon, role="approach", arm="east", entry_heading=(120, 200))
    wrapped = Zone(polygon=polygon, role="approach", arm="east", entry_heading=(350, 20))

    assert [plain.accepts_heading(h) for h in (119, 120, 200, 201)] == [False, True, True, False]
    assert [wrapped.accepts_heading(h) for h in (349, 355, 10, 21)] == [False, True, True, False]


def test_a_head_may_list_only_the_lamps_the_camera_can_see() -> None:
    data = copy.deepcopy(FULL)
    data["signal_heads"]["side_on"] = {"lamps": {"green": [700, 540, 3, 3]}}

    config = SiteConfig.model_validate(data)

    assert config.signal_heads["side_on"].controls == []
    assert config.lamp_regions()["side_on/green"] == (700, 540, 3, 3)
    assert config.heads_controlling("stopline_south") == ["sh_south_primary"]


def test_a_clip_is_as_long_as_its_event_type_says() -> None:
    clips = SiteConfig.model_validate(FULL).clips

    assert clips.lengths("red_light") == (5, 15)
    assert clips.lengths("incident_candidate") == (5, 55)
    assert clips.lengths() == (5, 55)


def test_a_clip_can_wait_for_an_event_to_be_bad_enough() -> None:
    clips = SiteConfig.model_validate(FULL).clips

    assert clips.wants("red_light", {})
    assert not clips.wants("amber_crossing", {})
    assert clips.wants("speeding", {"speed_mph": 45.0})
    assert not clips.wants("speeding", {"speed_mph": 44.9})
    assert not clips.wants("speeding", {})
