"""Vision service: logs the camera feed to Rerun and streams it to MediaMTX."""

import rerun as rr

from trafficcam.sources.picamera import PiCameraSource

GRPC_PORT = 9876
MAIN_SIZE = (2028, 1520)
LORES_SIZE = (1280, 960)
FPS = 15
JPEG_QUALITY = 75
LIVE_BITRATE = 6_000_000
# MediaMTX listens here for the `cam` path (deploy/mediamtx/mediamtx.yml).
LIVE_URL = "udp://127.0.0.1:1234?pkt_size=1316"


def main() -> None:
    rr.init("trafficcam")
    # Returns immediately; the server lives only as long as this process.
    uri = rr.serve_grpc(grpc_port=GRPC_PORT, server_memory_limit="512MiB")
    print(f"Rerun gRPC server at {uri}", flush=True)

    source = PiCameraSource(
        main_size=MAIN_SIZE,
        lores_size=LORES_SIZE,
        fps=FPS,
        bitrate=LIVE_BITRATE,
        live_url=LIVE_URL,
    )
    for frame in source.frames():
        rr.log("camera", rr.Image(frame).compress(jpeg_quality=JPEG_QUALITY))


if __name__ == "__main__":
    main()
