# 0004. Vision is a Python process on the host

Date: 2026-10-04. Status: Accepted.

## Context

Vision is the only part that touches frames. It needs direct access to the camera and to the Hailo accelerator, and on Raspberry Pi OS the libraries for both (picamera2 and the Hailo bindings) are installed with apt against the system Python. Everything else on the Pi runs in containers.

## Decision

Vision is written in Python and runs directly on the host, not in a container.

- It uses the system Python in a virtual environment created with `--system-site-packages`, so apt-installed libraries are importable. Other dependencies come from the lockfile through `uv sync`.
- It runs as a systemd user unit, with lingering enabled so it starts at boot. A system unit would need sudo for every install and restart, and sudo on the Pi asks for a password.
- Hardware-specific imports are confined to two modules, `trafficcam.sources.picamera` and `trafficcam.inference.hailo`, so the rest of the package runs and is tested on the PC.
- The unit is `Type=notify` with a watchdog (see 0009). The notify call comes from `python3-systemd`, another apt package; it is imported in one place, and only when systemd started the process.

## Consequences

- Vision is deployed differently from the rest: files are copied over and the unit is restarted, where the containers are managed by Compose.
- Packages from the lockfile shadow the apt versions of the same package (numpy, for example). That has worked so far but is a place to look if an apt library breaks.
- PyAV and OpenCV are the exceptions: the lockfile skips both on the Pi, so picamera2 keeps the apt builds it was packaged with. The PC installs the same versions from PyPI. uv overrides and exclusions apply the same rule to packages that depend on them (supervision, trackers).
- A user unit cannot depend on system units, so the plan's ordering after time sync is done in the service: when running from the camera, vision waits for the clock to be synchronised before it starts. Without a network after a power cut, that means no vision and no live view until the clock syncs. The plan's requirement that the data drive is mounted still needs a mechanism.
- Logs are in the system journal: `journalctl --user-unit trafficcam-vision`.
- If a Python hot path proves too slow, the plan is a Rust extension for that path, not a rewrite.
