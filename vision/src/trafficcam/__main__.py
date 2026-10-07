"""Vision service: detects and tracks road users, and reports their trips through the junction."""

import argparse
import contextlib
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trafficcam.clips import PacketRing
from trafficcam.config import ConfigError, SiteConfig, load_site_config
from trafficcam.detectors import Detector
from trafficcam.detectors.banned_turn import BannedTurns
from trafficcam.detectors.box_junction import BoxJunctionStops
from trafficcam.detectors.conflicts import Conflicts
from trafficcam.detectors.incident import Incidents
from trafficcam.detectors.near_miss import NearMiss
from trafficcam.detectors.red_light import RedLight
from trafficcam.detectors.speeding import Speeding
from trafficcam.geometry import SceneGeometry
from trafficcam.health.watchdog import Watchdog, systemd_notify
from trafficcam.inference import InferenceBackend, NullBackend
from trafficcam.outputs import open_outputs
from trafficcam.passages import PassageBuilder
from trafficcam.pipeline import Pipeline, run
from trafficcam.signals import Signals
from trafficcam.signals.estimator import StageSequenceEstimator
from trafficcam.signals.lamps import LampRoiObserver
from trafficcam.signals.published import GroupStates
from trafficcam.signals.steps import StepFinder
from trafficcam.sources import Frame, FrameSource
from trafficcam.speed import SpeedMeter
from trafficcam.timesync import wait_for_clock_sync
from trafficcam.tracking.bytetrack import ByteTracker
from trafficcam.tracklog.replay import NO_IMAGE, TrackLogReplay, recent_lamps

LORES_SIZE = (1280, 960)
# At a start, the signal reading is shown this much of what was recorded before it, unless
# the newest of that is older than a restart takes.
PRIME_FROM = timedelta(minutes=5)
PRIME_STALE = timedelta(minutes=2)
PRIME_PING_EVERY = 500
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


def prime_signals(
    directory: Path, observer: LampRoiObserver, signals: Signals, watchdog: Watchdog
) -> int:
    """Show the signal reading the lamp colours recorded just before this start, so that a
    restart does not leave every head unknown until it has been seen lit and unlit again.

    The changes this turns up were published when they happened, and are not sent again.
    """
    frames = recent_lamps(directory, datetime.now(UTC), PRIME_FROM, PRIME_STALE)
    for index, (timestamp, lamps) in enumerate(frames):
        frame = Frame(index=-1, timestamp=timestamp, image=NO_IMAGE, samples=lamps)
        signals.update(timestamp, observer.read(frame), lamps)
        if index % (PRIME_PING_EVERY) == 0:
            watchdog.ping()
    return len(frames)


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
    if config.detectors.speed is not None:
        detectors.append(Speeding(config, config_hash))
    near_miss, incident = config.detectors.near_miss, config.detectors.incident
    if near_miss is not None:
        conflicts = Conflicts(
            near_miss, max(near_miss.pet_max_s, incident.contact_s if incident else 0)
        )
        detectors.append(NearMiss(config, config_hash, conflicts))
        if incident is not None:
            detectors.append(Incidents(config, config_hash, conflicts))
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
        estimator = StageSequenceEstimator(config) if config.signal_plan else None
        if estimator is not None and outputs.metrics is not None:
            outputs.metrics.watch_signal_plan(lambda: estimator.violations)
        steps = StepFinder(config) if estimator is not None else None
        signals = Signals(config, config_hash, estimator, steps)
        if outputs.metrics is not None:
            outputs.metrics.watch_doubted_heads(list(config.signal_heads), lambda: signals.doubted)
        speeds = SpeedMeter(config.detectors.speed) if config.detectors.speed else None
        observer = LampRoiObserver(config)
        if live and args.record_tracks is not None:
            primed = prime_signals(args.record_tracks, observer, signals, watchdog)
            print(f"signals start from {primed} recorded frames", flush=True)
        pipeline = Pipeline(
            backend,
            ByteTracker(config.tracking, config.camera.fps),
            SceneGeometry(config),
            PassageBuilder(config, config_hash, signals, speeds),
            open_detectors(config, config_hash, signals),
            signals,
            # A track log carries the signal states that were read when it was recorded.
            replay or observer,
            GroupStates(config, config_hash, signals) if config.signal_plan else None,
        )
        run(source, pipeline, outputs.observers, outputs.sinks, outputs.recorder)

    if outputs.rerun is not None:
        print("replay finished; serving Rerun until interrupted", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            signal.pause()


if __name__ == "__main__":
    main()
