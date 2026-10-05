"""Vision service: logs frames to Rerun and, from the camera, streams them to MediaMTX."""

import argparse
import contextlib
import signal
import sys
from pathlib import Path

import rerun as rr

from trafficcam.config import ConfigError, SiteConfig, load_site_config
from trafficcam.sources import FrameSource

GRPC_PORT = 9876
LORES_SIZE = (1280, 960)
JPEG_QUALITY = 75
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="path to site.yaml")
    parser.add_argument("--video", type=Path, help="replay this clip instead of using the camera")
    args = parser.parse_args()

    try:
        config, config_hash = load_site_config(args.config)
    except ConfigError as error:
        # Status 2 tells systemd not to restart: the config needs fixing first.
        print(f"invalid config: {error}", file=sys.stderr)
        sys.exit(2)
    print(f"camera {config.camera.id}, config {config_hash}", flush=True)

    rr.init("trafficcam")
    # Returns immediately; the server lives only as long as this process.
    uri = rr.serve_grpc(grpc_port=GRPC_PORT, server_memory_limit="512MiB")
    print(f"Rerun gRPC server at {uri}", flush=True)

    for frame in open_source(config, args.video).frames():
        rr.set_time("frame", sequence=frame.index)
        rr.set_time("capture_time", timestamp=frame.timestamp)
        rr.log("camera", rr.Image(frame.image).compress(jpeg_quality=JPEG_QUALITY))

    # Only a replay gets here. Keep serving so a viewer can still connect.
    print("replay finished; serving until interrupted", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        signal.pause()


if __name__ == "__main__":
    main()
