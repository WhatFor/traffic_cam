"""Vision service: detects and tracks road users in each frame and places them in the scene."""

import argparse
import contextlib
import signal
import sys
import time
from pathlib import Path

from trafficcam.config import ConfigError, SiteConfig, load_site_config
from trafficcam.debug.rerun_sink import RerunSink
from trafficcam.geometry import SceneGeometry
from trafficcam.inference import InferenceBackend
from trafficcam.sources import FrameSource
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="path to site.yaml")
    parser.add_argument("--video", type=Path, help="replay this clip instead of using the camera")
    parser.add_argument("--no-inference", action="store_true", help="log frames only")
    args = parser.parse_args()

    try:
        config, config_hash = load_site_config(args.config)
    except ConfigError as error:
        # Status 2 tells systemd not to restart: the config needs fixing first.
        print(f"invalid config: {error}", file=sys.stderr)
        sys.exit(2)
    print(f"camera {config.camera.id}, config {config_hash}", flush=True)

    sink = RerunSink(config, LORES_SIZE)
    tracker = ByteTracker(config.tracking, config.camera.fps)
    scene = SceneGeometry(config)
    with contextlib.ExitStack() as stack:
        backend = None if args.no_inference else stack.enter_context(open_backend(config))
        for frame in open_source(config, args.video).frames():
            sink.frame(frame)
            if backend is None:
                continue
            started = time.perf_counter()
            detections = backend.detect(frame)
            sink.detections(detections, (time.perf_counter() - started) * 1000)
            tracks = tracker.update(detections, frame.timestamp)
            sink.observation(scene.observe(tracks, frame.timestamp))

    # Only a replay gets here. Keep serving so a viewer can still connect.
    print("replay finished; serving until interrupted", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        signal.pause()


if __name__ == "__main__":
    main()
