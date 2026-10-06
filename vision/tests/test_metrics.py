"""Metrics are derived from frame results and passages, and served over HTTP."""

import dataclasses
import urllib.request
from datetime import timedelta

import numpy as np
from test_geometry import at
from test_pipeline import DRIVE_AND_GONE, a_clip, a_signal_change, an_event, run
from test_sinks import free_port

from trafficcam.contracts import ClipDeleted, SignalState
from trafficcam.health import metrics as metrics_module
from trafficcam.health.metrics import Metrics
from trafficcam.pipeline import FrameResult
from trafficcam.sources import Frame

PREFIX = "trafficcam_vision_"


def value(metrics: Metrics, name: str, **labels: str) -> float | None:
    return metrics.registry.get_sample_value(PREFIX + name, labels)


def with_image(result: FrameResult, seconds: float, level: int) -> FrameResult:
    image = np.full((96, 128, 3), level, dtype=np.uint8)
    frame = Frame(result.frame.index, at(0) + timedelta(seconds=seconds), image)
    return FrameResult(
        frame,
        result.detections,
        result.inference_ms,
        result.observation,
        result.passages,
        result.events,
        result.signals,
        result.signal_changes,
    )


def test_a_drive_is_counted_frame_by_frame() -> None:
    metrics = Metrics()
    _, results = run(DRIVE_AND_GONE)

    for result in results[:10]:
        metrics.observe(result)

    assert value(metrics, "frames_total") == 10
    assert value(metrics, "frame_interval_seconds_count") == 9
    assert value(metrics, "frame_interval_seconds_bucket", le="0.07") == 9
    assert value(metrics, "inference_seconds_count") == 10
    assert value(metrics, "detections") == 1
    assert value(metrics, "active_tracks") == 1

    for result in results[10:]:
        metrics.observe(result)

    assert value(metrics, "detections") == 0
    assert value(metrics, "active_tracks") == 0


def test_passages_are_counted_by_whether_they_have_a_movement() -> None:
    metrics = Metrics()
    _, results = run(DRIVE_AND_GONE)
    (passage,) = [passage for result in results for passage in result.passages]

    assert value(metrics, "passages_total", complete="true") == 0
    metrics.passage(passage)
    metrics.passage(passage.model_copy(update={"movement": None}))
    metrics.passage(passage)

    assert value(metrics, "passages_total", complete="true") == 2
    assert value(metrics, "passages_total", complete="false") == 1
    assert value(metrics, "last_passage_timestamp_seconds") == passage.ts.timestamp()


def test_events_are_counted_by_type() -> None:
    metrics = Metrics()

    metrics.event(an_event())
    metrics.event(an_event())
    metrics.event(an_event("red_light"))

    assert value(metrics, "events_total", type="box_junction_stop") == 2
    assert value(metrics, "events_total", type="red_light") == 1
    assert (value(metrics, "last_event_timestamp_seconds", type="red_light") or 0) > 0


def test_signal_changes_are_counted_and_unknown_heads_flagged() -> None:
    metrics = Metrics()
    _, (result, *_) = run(DRIVE_AND_GONE)
    states = {"near": SignalState.green, "far": SignalState.unknown}

    metrics.signal(a_signal_change("near"))
    metrics.signal(a_signal_change("near"))
    metrics.observe(dataclasses.replace(result, signals=states))

    assert value(metrics, "signal_changes_total", head="near") == 2
    assert value(metrics, "signal_unknown", head="near") == 0
    assert value(metrics, "signal_unknown", head="far") == 1


def test_drop_counts_and_mqtt_state_are_read_when_scraped() -> None:
    metrics = Metrics()
    dropped = {"mqtt": 0}
    connected = [False]
    metrics.watch_dropped("mqtt", lambda: dropped["mqtt"])
    metrics.watch_mqtt(lambda: connected[0])

    assert value(metrics, "dropped_total", queue="mqtt") == 0
    assert value(metrics, "mqtt_connected") == 0

    dropped["mqtt"] = 3
    connected[0] = True

    assert value(metrics, "dropped_total", queue="mqtt") == 3
    assert value(metrics, "mqtt_connected") == 1


def test_image_quality_is_measured_once_per_interval() -> None:
    metrics = Metrics()
    _, (result, *_) = run(DRIVE_AND_GONE)
    interval = metrics_module.IMAGE_QUALITY_INTERVAL.total_seconds()

    metrics.observe(with_image(result, 0, level=40))
    assert value(metrics, "image_brightness") == 40

    metrics.observe(with_image(result, interval - 0.1, level=200))
    assert value(metrics, "image_brightness") == 40

    metrics.observe(with_image(result, interval, level=200))
    assert value(metrics, "image_brightness") == 200
    assert value(metrics, "image_sharpness") == 0


def test_frames_without_a_picture_are_not_measured() -> None:
    metrics = Metrics()
    _, (result, *_) = run(DRIVE_AND_GONE)

    metrics.observe(result)
    metrics.observe(with_image(result, 1, level=40))

    assert value(metrics, "image_brightness") == 40


def test_metrics_are_served_over_http() -> None:
    metrics = Metrics()
    port = free_port()
    metrics.serve(port)
    _, results = run(DRIVE_AND_GONE)
    metrics.observe(results[0])

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as response:
        body = response.read().decode()
    metrics.close()

    assert "trafficcam_vision_frames_total 1.0" in body
    assert "process_resident_memory_bytes" in body


def test_clips_are_counted_and_their_folder_measured() -> None:
    metrics = Metrics()
    clip = a_clip()
    metrics.watch_clips_folder(lambda: 45_000_000)
    metrics.watch_dropped("clips", lambda: 2)

    metrics.clip(clip)
    metrics.clip_deleted(ClipDeleted.model_validate({"id": clip.id, "ts": clip.ts, "camera": "x"}))

    assert value(metrics, "clips_total") == 1
    assert value(metrics, "clips_deleted_total") == 1
    assert value(metrics, "clips_bytes") == 45_000_000
    assert value(metrics, "dropped_total", queue="clips") == 2
