"""Hello-world vision service: serves a Rerun gRPC server and logs a sine wave."""

import math
import time

import rerun as rr

GRPC_PORT = 9876
RATE_HZ = 10
PERIOD_S = 5.0


def main() -> None:
    rr.init("trafficcam")
    # Returns immediately; the server lives only as long as this process.
    uri = rr.serve_grpc(grpc_port=GRPC_PORT, server_memory_limit="512MiB")
    print(f"Rerun gRPC server at {uri}", flush=True)

    start = time.monotonic()
    while True:
        t = time.monotonic() - start
        rr.log("test/sine", rr.Scalars(math.sin(math.tau * t / PERIOD_S)))
        time.sleep(1 / RATE_HZ)


if __name__ == "__main__":
    main()
