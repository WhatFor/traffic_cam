"""Vision service: detects and tracks road users in each frame and places them in the scene."""

import argparse
import contextlib
import os
import signal
import sys
import time
from pathlib import Path

from trafficcam.config import ConfigError, SiteConfig, load_site_config
from trafficcam.contracts import Passage
from trafficcam.debug.rerun_sink import RerunSink
from trafficcam.geometry import SceneGeometry
from trafficcam.inference import InferenceBackend
from trafficcam.passages import PassageBuilder
from trafficcam.sinks import EventSink
from trafficcam.sinks.jsonl import JsonlSink
from trafficcam.sinks.mqtt import MqttSink
from trafficcam.sources import FrameSource
from trafficcam.timesync import wait_for_clock_sync
from trafficcam.tracking.bytetrack import ByteTracker

LORES_SIZE = (1280, 960)
LIVE_BITRATE = 6_000_000
# MediaMTX listens here for the `cam` path (deploy/mediamtx/mediamtx.yml).
LIVE_URL = "udp://127.0.0.1:1234?pkt_size=1316"


def open_source(config: SiteConfig, video: Path | None) -> FrameSource:
    if video is not None:
        from trafficcam.sources.video_file import VideoFileSource

        return VideoFileSource(video, size=LORES_SIZE)

    # Imported here because picamera2 only exists on the Pi.
    from trafficcam.sources.picamera import PiCameraSource

    return PiCameraSource(
        main_size=config.camera.size,
        lores_size=LORES_SIZE,
        fps=config.camera.fps,
        bitrate=LIVE_BITRATE,
        live_url=LIVE_URL,
    )


def open_backend(config: SiteConfig) -> contextlib.AbstractContextManager[InferenceBackend]:
    # Imported here because the Hailo bindings only exist on the Pi.
    from trafficcam.inference.hailo import HailoBackend

    return HailoBackend(config.inference, config.camera.size)


def open_sinks(config: SiteConfig, args: argparse.Namespace) -> list[EventSink]:
    sinks: list[EventSink] = []
    if args.jsonl is not None:
        sinks.append(JsonlSink(args.jsonl))
    # A replay must never reach the broker: its passages would be stored as if they were new.
    if args.video is None:
        password = os.environ.get("MQTT_PASSWORD")
        if not password:
            print("MQTT_PASSWORD is not set", file=sys.stderr)
            sys.exit(2)
        sinks.append(
            MqttSink(
                host=os.environ.get("MQTT_HOST", "127.0.0.1"),
                port=int(os.environ.get("MQTT_PORT", "1883")),
                password=password,
                camera=config.camera.id,
            )
        )
    return sinks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="path to site.yaml")
    parser.add_argument("--video", type=Path, help="replay this clip instead of using the camera")
    parser.add_argument("--no-inference", action="store_true", help="log frames only")
    parser.add_argument("--jsonl", type=Path, help="also write passages to this file")
    args = parser.parse_args()

    try:
        config, config_hash = load_site_config(args.config)
    except ConfigError as error:
        # Status 2 tells systemd not to restart: the config needs fixing first.
        print(f"invalid config: {error}", file=sys.stderr)
        sys.exit(2)
    print(f"camera {config.camera.id}, config {config_hash}", flush=True)

    # systemd stops the service with SIGTERM; exiting normally lets the sinks close.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if args.video is None:
        wait_for_clock_sync()

    rerun = RerunSink(config, LORES_SIZE)
    tracker = ByteTracker(config.tracking, config.camera.fps)
    scene = SceneGeometry(config)
    passages = PassageBuilder(config, config_hash)

    def deliver(closed: list[Passage]) -> None:
        rerun.passages(closed)
        for passage in closed:
            for sink in sinks:
                sink.passage(passage)

    last_timestamp = None
    with contextlib.ExitStack() as stack:
        sinks = open_sinks(config, args)
        for sink in sinks:
            stack.callback(sink.close)
        backend = None if args.no_inference else stack.enter_context(open_backend(config))
        for frame in open_source(config, args.video).frames():
            rerun.frame(frame)
            if backend is None:
                continue
            started = time.perf_counter()
            detections = backend.detect(frame)
            rerun.detections(detections, (time.perf_counter() - started) * 1000)
            tracks = tracker.update(detections, frame.timestamp)
            observation = scene.observe(tracks, frame.timestamp)
            rerun.observation(observation)
            deliver(passages.update(observation, frame.timestamp))
            last_timestamp = frame.timestamp
        # Only a replay gets here.
        if last_timestamp is not None:
            deliver(passages.flush(last_timestamp))

    print("replay finished; serving Rerun until interrupted", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        signal.pause()


if __name__ == "__main__":
    main()
