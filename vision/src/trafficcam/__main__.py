"""Vision service: detects and tracks road users, and reports their trips through the junction."""

import argparse
import contextlib
import os
import signal
import sys
from pathlib import Path

from trafficcam.config import ConfigError, SiteConfig, load_site_config
from trafficcam.geometry import SceneGeometry
from trafficcam.inference import InferenceBackend
from trafficcam.passages import PassageBuilder
from trafficcam.pipeline import FrameObserver, Pipeline, run
from trafficcam.sinks import EventSink
from trafficcam.sinks.jsonl import JsonlSink
from trafficcam.sinks.mqtt import MqttSink
from trafficcam.sources import FrameSource
from trafficcam.timesync import wait_for_clock_sync
from trafficcam.tracking.bytetrack import ByteTracker
from trafficcam.tracklog.replay import TrackLogReplay
from trafficcam.tracklog.writer import TrackLogWriter

LORES_SIZE = (1280, 960)
LIVE_BITRATE = 6_000_000
# MediaMTX listens here for the `cam` path (deploy/mediamtx/mediamtx.yml).
LIVE_URL = "udp://127.0.0.1:1234?pkt_size=1316"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="path to site.yaml")
    replay = parser.add_mutually_exclusive_group()
    replay.add_argument("--video", type=Path, help="replay this clip instead of using the camera")
    replay.add_argument(
        "--tracks",
        type=Path,
        nargs="+",
        metavar="FILE",
        help="replay the detections in these track logs instead of using the camera",
    )
    parser.add_argument("--no-inference", action="store_true", help="log frames only")
    parser.add_argument("--jsonl", type=Path, help="also write passages to this file")
    parser.add_argument("--debug-rerun", action="store_true", help="serve a Rerun debug view")
    parser.add_argument(
        "--record-tracks", type=Path, metavar="DIR", help="write track logs into this directory"
    )
    return parser.parse_args()


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


def open_mqtt(config: SiteConfig) -> MqttSink:
    password = os.environ.get("MQTT_PASSWORD")
    if not password:
        print("MQTT_PASSWORD is not set", file=sys.stderr)
        sys.exit(2)
    return MqttSink(
        host=os.environ.get("MQTT_HOST", "127.0.0.1"),
        port=int(os.environ.get("MQTT_PORT", "1883")),
        password=password,
        camera=config.camera.id,
    )


def main() -> None:
    args = parse_args()
    try:
        config, config_hash = load_site_config(args.config)
    except ConfigError as error:
        # Status 2 tells systemd not to restart: the config needs fixing first.
        print(f"invalid config: {error}", file=sys.stderr)
        sys.exit(2)
    print(f"camera {config.camera.id}, config {config_hash}", flush=True)

    # systemd stops the service with SIGTERM; exiting normally lets everything close.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    live = args.video is None and args.tracks is None
    if live:
        wait_for_clock_sync()

    sinks: list[EventSink] = []
    observers: list[FrameObserver] = []
    rerun = None
    with contextlib.ExitStack() as stack:
        if args.jsonl is not None:
            sinks.append(JsonlSink(args.jsonl))
            stack.callback(sinks[-1].close)
        # A replay must never reach the broker: its passages would be stored as if they were new.
        if live:
            sinks.append(open_mqtt(config))
            stack.callback(sinks[-1].close)
        if args.debug_rerun:
            # Imported here so the Rerun SDK is only loaded when it is used.
            from trafficcam.debug.rerun_sink import RerunSink

            rerun = RerunSink(config, LORES_SIZE)
            sinks.append(rerun)
            observers.append(rerun)
        if args.record_tracks is not None:
            observers.append(
                TrackLogWriter(
                    args.record_tracks,
                    camera=config.camera.id,
                    config_hash=config_hash,
                    wait_when_full=not live,
                )
            )
            stack.callback(observers[-1].close)

        replay = TrackLogReplay(args.tracks, config_hash) if args.tracks else None
        source = replay or open_source(config, args.video)
        if args.no_inference:
            for frame in source.frames():
                if rerun is not None:
                    rerun.frame(frame)
        else:
            pipeline = Pipeline(
                replay or stack.enter_context(open_backend(config)),
                ByteTracker(config.tracking, config.camera.fps),
                SceneGeometry(config),
                PassageBuilder(config, config_hash),
            )
            run(source, pipeline, observers, sinks)

    if rerun is not None:
        print("replay finished; serving Rerun until interrupted", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            signal.pause()


if __name__ == "__main__":
    main()
