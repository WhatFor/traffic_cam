"""Vision service: serves a Rerun gRPC server and logs the camera feed to it."""

import rerun as rr

from trafficcam.sources.picamera import PiCameraSource

GRPC_PORT = 9876
SIZE = (1280, 960)
FPS = 15
JPEG_QUALITY = 75


def main() -> None:
    rr.init("trafficcam")
    # Returns immediately; the server lives only as long as this process.
    uri = rr.serve_grpc(grpc_port=GRPC_PORT, server_memory_limit="512MiB")
    print(f"Rerun gRPC server at {uri}", flush=True)

    for frame in PiCameraSource(SIZE, FPS).frames():
        rr.log("camera", rr.Image(frame).compress(jpeg_quality=JPEG_QUALITY))


if __name__ == "__main__":
    main()
