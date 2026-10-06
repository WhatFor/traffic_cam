"""Vision service: detects and tracks road users, and reports their trips through the junction."""

import argparse
import contextlib
import signal
import sys
from pathlib import Path

from trafficcam.clips import PacketRing
from trafficcam.config import ConfigError, SiteConfig, load_site_config
from trafficcam.detectors import Detector
from trafficcam.detectors.banned_turn import BannedTurns
from trafficcam.detectors.box_junction import BoxJunctionStops
from trafficcam.detectors.red_light import RedLight
from trafficcam.geometry import SceneGeometry
from trafficcam.health.watchdog import Watchdog, systemd_notify
from trafficcam.inference import InferenceBackend, NullBackend
from trafficcam.outputs import open_outputs
from trafficcam.passages import PassageBuilder
from trafficcam.pipeline import Pipeline, run
from trafficcam.signals import Signals
from trafficcam.signals.lamps import LampRoiObserver
from trafficcam.sources import FrameSource
from trafficcam.timesync import wait_for_clock_sync
from trafficcam.tracking.bytetrack import ByteTracker
from trafficcam.tracklog.replay import TrackLogReplay

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
    parser.add_argument(
        "--no-inference", action="store_true", help="detect nothing; signals are still read"
    )
    parser.add_argument("--jsonl", type=Path, help="also write passages to this file")
    parser.add_argument("--debug-rerun", action="store_true", help="serve a Rerun debug view")
    parser.add_argument(
        "--record-tracks", type=Path, metavar="DIR", help="write track logs into this directory"
    )
    parser.add_argument(
        "--metrics-port", type=int, metavar="PORT", help="serve Prometheus metrics on localhost"
    )
    return parser.parse_args()


def open_source(config: SiteConfig, video: Path | None, ring: PacketRing | None) -> FrameSource:
    if video is not None:
        from trafficcam.sources.video_file import VideoFileSource

        return VideoFileSource(video, size=LORES_SIZE, regions=config.lamp_regions())

    # Imported here because picamera2 only exists on the Pi.
    from trafficcam.sources.picamera import PiCameraSource

    return PiCameraSource(
        main_size=config.camera.size,
        lores_size=LORES_SIZE,
        fps=config.camera.fps,
        bitrate=LIVE_BITRATE,
        live_url=LIVE_URL,
        regions=config.lamp_regions(),
        ring=ring,
    )


def open_backend(config: SiteConfig) -> contextlib.AbstractContextManager[InferenceBackend]:
    # Imported here because the Hailo bindings only exist on the Pi.
    from trafficcam.inference.hailo import HailoBackend

    return HailoBackend(config.inference, config.camera.size)


def open_detectors(config: SiteConfig, config_hash: str, signals: Signals) -> list[Detector]:
    """The detectors that site.yaml has settings for."""
    detectors: list[Detector] = []
    if config.detectors.box_junction is not None:
        detectors.append(BoxJunctionStops(config, config_hash))
    if config.detectors.banned_turns is not None:
        detectors.append(BannedTurns(config, config_hash, signals))
    if config.detectors.red_light is not None:
        detectors.append(RedLight(config, config_hash, signals))
    return detectors


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
    notify = systemd_notify()
    watchdog = Watchdog.from_environment(notify)
    # From here on systemd expects watchdog pings.
    notify("READY=1")
    live = args.video is None and args.tracks is None
    if live:
        notify("STATUS=waiting for the clock to synchronise")
        wait_for_clock_sync(waiting=watchdog.ping)
    notify("STATUS=running")

    with contextlib.ExitStack() as stack:
        # The camera's encoded video, kept for clips to be cut from.
        ring = PacketRing(config.clips.buffer_s) if live else None
        outputs = open_outputs(stack, config, config_hash, args, LORES_SIZE, watchdog, ring)
        replay = TrackLogReplay(args.tracks, config_hash) if args.tracks else None
        source = replay or open_source(config, args.video, ring)
        if replay is not None:
            backend = replay
        elif args.no_inference:
            backend = NullBackend()
        else:
            backend = stack.enter_context(open_backend(config))
        signals = Signals(config, config_hash)
        pipeline = Pipeline(
            backend,
            ByteTracker(config.tracking, config.camera.fps),
            SceneGeometry(config),
            PassageBuilder(config, config_hash, signals),
            open_detectors(config, config_hash, signals),
            signals,
            # A track log carries the signal states that were read when it was recorded.
            replay or LampRoiObserver(config),
        )
        run(source, pipeline, outputs.observers, outputs.sinks, outputs.recorder)

    if outputs.rerun is not None:
        print("replay finished; serving Rerun until interrupted", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            signal.pause()


if __name__ == "__main__":
    main()
