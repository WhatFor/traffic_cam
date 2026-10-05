# 0004. Vision is a Python process on the host

Date: 2026-10-04. Status: Accepted.

## Context

Vision is the only part that touches frames. It needs direct access to the camera and to the Hailo accelerator, and on Raspberry Pi OS the libraries for both (picamera2 and the Hailo bindings) are installed with apt against the system Python. Everything else on the Pi runs in containers.

## Decision

Vision is written in Python and runs directly on the host, not in a container.

- It uses the system Python in a virtual environment created with `--system-site-packages`, so apt-installed libraries are importable. Other dependencies come from the lockfile through `uv sync`.
- It runs as a systemd user unit, with lingering enabled so it starts at boot. A system unit would need sudo for every install and restart, and sudo on the Pi asks for a password.
- Hardware-specific imports are confined to two modules, `trafficcam.sources.picamera` and `trafficcam.inference.hailo`, so the rest of the package runs and is tested on the PC.

## Consequences

- Vision is deployed differently from the rest: files are copied over and the unit is restarted, where the containers are managed by Compose.
- Packages from the lockfile shadow the apt versions of the same package (numpy, for example). That has worked so far but is a place to look if an apt library breaks.
- PyAV is the exception: the lockfile skips it on the Pi, so picamera2's encoder keeps the apt build it was packaged with. The PC installs the same version from PyPI. A uv override applies the same rule to packages that depend on PyAV, such as supervision.
- A user unit cannot depend on system units. The plan's ordering after time sync and its requirement that the data drive is mounted need another mechanism, such as checks in the service itself.
- Logs are in the system journal: `journalctl --user-unit trafficcam-vision`.
- If a Python hot path proves too slow, the plan is a Rust extension for that path, not a rewrite.
