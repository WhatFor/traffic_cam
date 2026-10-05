"""Vision service: detects road users in each frame and logs frames and detections to Rerun."""

import argparse
import contextlib
import signal
import sys
import time
from pathlib import Path

import rerun as rr
import supervision as sv

from trafficcam.config import ConfigError, SiteConfig, load_site_config
from trafficcam.inference import COCO_CLASS_IDS, InferenceBackend
from trafficcam.sources import FrameSource

GRPC_PORT = 9876
LORES_SIZE = (1280, 960)
JPEG_QUALITY = 75
LIVE_BITRATE = 6_000_000
# MediaMTX listens here for the `cam` path (deploy/mediamtx/mediamtx.yml).
LIVE_URL = "udp://127.0.0.1:1234?pkt_size=1316"

CLASS_NAMES = {class_id: name.value for name, class_id in COCO_CLASS_IDS.items()}


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


def log_detections(detections: sv.Detections, scale: float) -> None:
    """Log boxes over the camera image, which is `scale` times the size of the full frame."""
    confidence = detections.confidence if detections.confidence is not None else []
    class_id = detections.class_id if detections.class_id is not None else []
    rr.log(
        "camera/detections",
        rr.Boxes2D(
            array=detections.xyxy * scale,
            array_format=rr.Box2DFormat.XYXY,
            class_ids=class_id,
            labels=[
                f"{CLASS_NAMES[int(c)]} {score:.0%}"
                for c, score in zip(class_id, confidence, strict=True)
            ],
        ),
    )


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

    rr.init("trafficcam")
    # Returns immediately; the server lives only as long as this process.
    uri = rr.serve_grpc(grpc_port=GRPC_PORT, server_memory_limit="512MiB")
    print(f"Rerun gRPC server at {uri}", flush=True)

    scale = LORES_SIZE[0] / config.camera.size[0]
    with contextlib.ExitStack() as stack:
        backend = None if args.no_inference else stack.enter_context(open_backend(config))
        for frame in open_source(config, args.video).frames():
            rr.set_time("frame", sequence=frame.index)
            rr.set_time("capture_time", timestamp=frame.timestamp)
            rr.log("camera", rr.Image(frame.image).compress(jpeg_quality=JPEG_QUALITY))
            if backend is not None:
                started = time.perf_counter()
                detections = backend.detect(frame)
                rr.log("stats/inference_ms", rr.Scalars((time.perf_counter() - started) * 1000))
                log_detections(detections, scale)

    # Only a replay gets here. Keep serving so a viewer can still connect.
    print("replay finished; serving until interrupted", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        signal.pause()


if __name__ == "__main__":
    main()
