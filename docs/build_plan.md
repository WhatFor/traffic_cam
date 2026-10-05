# Traffic camera — build plan

Oct 4, 2026 · @C

## How to use this plan

This plan briefs a Claude Code agent to build a 24/7 traffic-monitoring system on a Raspberry Pi 5. The camera watches a UK city-centre signalised junction, detects traffic events and stores them for dashboards. Phase 0 (hardware bring-up) is done; start at Phase 1 of the roadmap and work down.

Working agreements for the agent:

- Work in the repo on the NixOS PC. Reach the Pi over SSH as `chris@pi` (LAN) or by its Tailscale name. Deploy with `just deploy`, not by editing files on the Pi.
- Keep hardware-specific code (picamera2, Hailo) behind interfaces, so everything else runs and tests on the x86 PC.
- Ask before anything destructive or hard to undo on the Pi: partitioning or formatting disks, deleting recordings or clips, editing `/etc/fstab` or boot config, removing or upgrading Hailo packages or the kernel.
- Never upgrade `hailo-all`, HailoRT or the kernel as a side effect. The Hailo driver, firmware and runtime versions must match each other.
- Never commit video, images or secrets. Footage contains identifiable people and number plates.
- Pin `rerun-sdk` on the Pi to the viewer version on the PC (currently 0.37.2).
- One roadmap step per PR. Record decisions as ADRs in `docs/adr/` and update this plan when a decision changes.
- Detectors are pure functions with fixture-based tests. Geometry and thresholds live in `config/site.yaml`, not in code.
- When something can only be verified with the live camera (night lighting, signal visibility), leave an explicit checklist item rather than guessing.

## Goals and requirements

The system turns one fixed camera into a time series of junction events: every vehicle passage, every violation, and signal state changes. IDs below are referenced from the roadmap.

| ID | Functional requirement | Notes |
| --- | --- | --- |
| F1 | Track road users: cars, vans, buses, trucks, motorcycles, bicycles | Stable IDs across frames; pedestrians tracked for incidents |
| F2 | Read the state of visible signal heads | Red, red+amber, green, amber, unknown |
| F3 | Estimate the phase of signal heads that are not visible | Experimental; may not pan out (see Signal state) |
| F4 | Detectors: red-light running, a specific left turn, speeding, crashes, stopping in the yellow box | Amber crossings recorded too; crash is a stretch goal |
| F5 | Store passages and events as time series for custom dashboards | Grafana first; other front ends later |
| F6 | On a detected crash, save 5 s before and 55 s after to the SSD | Manual trigger too, for testing |
| F7 | Email on a detected crash, with a still frame and a clip link | Optional phone push via ntfy |
| F8 | Monitor system health over the network, with alerts | Camera, inference, disk, temperature, services |
| F9 | Live view over the network at low CPU cost | Re-use the existing encode; no transcoding |
| F10 | Later: public stats on a separate VPS | Aggregates only; never video; not hosted on the Pi |

| ID | Non-functional requirement |
| --- | --- |
| N1 | Runs unattended 24/7 on the Pi alone, with the PC off; recovers from crashes, hangs, reboots and power cuts |
| N2 | Replayable: the same pipeline runs on recorded clips for development and regression tests |
| N3 | Rates, not just counts: every passage is logged, so violations can be expressed per crossing |
| N4 | Versioned site config: every event records the hash of the config that produced it |
| N5 | Detects camera movement, since all zones are pixel coordinates |
| N6 | Privacy: short clip retention; only aggregates ever leave the Pi |
| N7 | Evolvable: detectors are plugins; adding one touches neither capture nor storage |
| N8 | Fits the Pi 5's 8 GB RAM; inference on the Hailo; CPU headroom kept for video encoding |
| N9 | Remote access over Tailscale only; nothing exposed to the internet |

## Site, hardware and environment

Everything runs on one Raspberry Pi 5 (8 GB) with a Hailo-8 accelerator and an HQ Camera, mounted indoors behind a window several storeys above the junction.

| Part | Detail | Notes |
| --- | --- | --- |
| Computer | Raspberry Pi 5, 8 GB, Raspberry Pi OS Trixie 64-bit (headless) | Hostname `pi`, user `chris`; system Python 3.13; libcamera 0.7.2 |
| Accelerator | Raspberry Pi AI HAT+, Hailo-8 (26 TOPS) | Uses the Pi 5's single PCIe lane, so storage is on USB |
| Camera | Raspberry Pi HQ Camera (Sony IMX477), C/CS mount, on a CSI port | Full field of view at 2028×1520 (4:3, 2×2 binned) |
| Lens | CS-mount lens, around 6 mm | Focused and locked; aperture set wide for night light. Confirm exact model |
| Storage | Toshiba USB 3 external drive (USB ID `0480:a303`), ext4, label `data` | Mounted at `/mnt/data` by UUID; uses `usb-storage` (no UAS), which is fine for this load |
| Power | Official 27 W USB-C supply recommended | Needed for full USB current to the drive; confirm it is in use |
| Network | Home LAN; Tailscale for remote access | Confirm the Pi has joined the tailnet |
| Dev PC | NixOS (`chris-pc`): flakes, Home Manager, GNOME on Wayland | Rerun viewer 0.37.2 from nixpkgs; mpv via `nix run nixpkgs#mpv` |

The scene:

- **Foreground approach**: multi-lane, heading away from the camera into the junction, with a stop line and an advanced cycle stop box. This is the best candidate for red-light detection: large in frame, and its signals face the camera.
- **Centre**: a large yellow box junction.
- **Other features**: pedestrian crossings on several arms and a long arm receding into the distance.
- **Signal heads**: at least three show lit lamps towards the camera. Others are seen from behind.
- **Lighting**: evening sun comes from behind the camera, so it can light up unlit lamps. Morning sun is probably in front, so expect lens flare. Street lighting at night.

## Current state: Phase 0 done

The hardware works end to end: camera framed and focused, Hailo detection running, results visible on the PC in Rerun, SSD mounted. No production code exists yet; two prototype scripts prove the pieces.

| Area | State | Detail |
| --- | --- | --- |
| Camera | Done | Framed, focused and aperture set using a zoomed live stream to the PC |
| Hailo | Done | `hailo-all` installed; `hailortcli fw-control identify` reports a Hailo-8 |
| Detection demo | Done | picamera2 Hailo example adapted for headless use; boxes verified on live video |
| `detect_stream.py` | Prototype | Hailo detection with boxes burned into an H.264 stream on TCP port 8888 |
| `detect_rerun.py` | Prototype, working | Logs a JPEG frame, boxes, labels, inference time and vehicle count to Rerun; serves gRPC on port 9876 with a 512 MiB buffer cap |
| Python environment | Done | `~/cam/.venv`, created with `--system-site-packages`; `rerun-sdk==0.37.2` installed |
| Rerun | Done | Viewer 0.37.2 on NixOS; `rerun --connect rerun+http://<pi-ip>:9876/proxy` works |
| SSD | Done | ext4 at `/mnt/data`, fstab by UUID with `noatime,nofail,x-systemd.device-timeout=10s`; `fstrim.timer` enabled |
| Recordings | Working | One-minute test clips recorded with `rpicam-vid` and copied to the PC |

The prototypes live on the Pi in `~/cam/examples/picamera2-examples/examples/hailo/`, next to the `coco.txt` labels file. Copy them into the repo under `tools/prototypes/` as reference; the vision service replaces them.

What the detection tests showed:

- **Threshold**: 0.3 finds far more vehicles than 0.5, with few extra false positives.
- **Model build**: the default model, `yolov8s_h8l.hef`, was compiled for the smaller Hailo-8L, and HailoRT warns about lower performance on the Hailo-8. Use a `_h8` build if `/usr/share/hailo-models/` has one.
- **Small vehicles**: squashing the whole 4:3 frame into the model's 640×640 input misses small and distant vehicles. Running the model on crops of the junction is required.
- **False positive**: a no-stopping sign with its "End" plate was detected as a person (41%).
- **No van class**: the COCO classes have no van, so vans come out as truck or car.
- **Signal housings** are detected as the COCO "traffic light" class. Lamp state still needs its own logic.

Commands verified on this hardware:

```bash
# Live view on the PC (Pi side, then PC side)
rpicam-vid -t 0 -n --width 2028 --height 1520 --framerate 15 \
  --codec libav --libav-format mpegts --low-latency -o "tcp://0.0.0.0:8888?listen=1"
nix run nixpkgs#mpv -- --profile=low-latency --untimed tcp://<pi-ip>:8888

# Focus aid: full-resolution crop of a region (fractions of the frame)
rpicam-vid -t 0 -n --mode 4056:3040:12:P --roi 0.4,0.4,0.2,0.2 --width 1600 --height 1200 \
  --framerate 10 --codec libav --libav-format mpegts --low-latency -o "tcp://0.0.0.0:8888?listen=1"

# Hailo demo with boxes drawn into the stream
rpicam-vid ... --post-process-file /usr/share/rpi-camera-assets/hailo_yolov8_inference.json ...

# One-minute timestamped recording
rpicam-vid -t 60s -n --width 2028 --height 1520 --framerate 15 -b 12000000 \
  -o "$(date +%Y%m%d-%H%M%S).mp4"

# Copy recordings to the PC (run on the PC)
rsync -avP 'chris@pi:~/cam/*.mp4' ~/clips/
```

## Architecture overview

Frames never leave the vision process. It publishes passages, events and signal changes to Mosquitto. `ingest` writes them to TimescaleDB for Grafana and sends incident notifications.

&#91;embedded content: system architecture · vision process, Compose stack, external consumers\]

The single H.264 encode feeds both the clip buffer and MediaMTX's live view. VictoriaMetrics scrapes every service's `/metrics` for health dashboards and alerts. Everything is reached over Tailscale; nothing is exposed to the internet.

## Tech stack

Python owns everything that touches frames; .NET owns storage, APIs and notifications; infrastructure runs as containers. Pin every version the agent installs; pick the current stable release where none is given here.

| Layer | Choice | Notes |
| --- | --- | --- |
| OS | Raspberry Pi OS Trixie 64-bit, headless | Required for Hailo and libcamera support |
| Vision runtime | System Python 3.13 in a venv with `--system-site-packages` | picamera2 and the Hailo bindings come from apt |
| Python tooling | uv (lockfile), ruff, pytest, pyright | `uv venv --system-site-packages` on the Pi |
| Camera | picamera2 (libcamera) | Two ISP streams: one for encoding, one for inference |
| Inference | HailoRT via `picamera2.devices.Hailo` | YOLOv8s from `/usr/share/hailo-models/`; Hailo-8 build preferred |
| Detection utilities | `supervision` | Detections, PolygonZone, LineZone, InferenceSlicer, annotators |
| Tracking | `trackers` (`ByteTrackTracker`) | `sv.ByteTrack` is deprecated; drop `tracker_id == -1` before zone logic |
| Image processing | OpenCV (`python3-opencv`), numpy | Lamp brightness, image quality, homography |
| Debug view | Rerun SDK 0.37.2 | Pinned to the PC's viewer; off by default in production |
| Message bus | Mosquitto (MQTT 5) | paho-mqtt in Python, MQTTnet in .NET |
| Contracts | JSON Schema in `contracts/` | Generated pydantic models and C# types |
| Ingest, API, notifier | .NET 10 worker plus minimal API | Npgsql, DbUp migrations, MailKit, prometheus-net; runs in Microsoft's stock ASP.NET runtime image |
| Database | PostgreSQL with TimescaleDB | Time-partitioned tables (hypertables) and continuous aggregates (rollups) |
| Metrics | VictoriaMetrics single-node, node\_exporter | Prometheus-compatible; `prometheus_client` in Python |
| Dashboards and alerts | Grafana, provisioned from the repo | Alerts by email and ntfy |
| Video | picamera2 `H264Encoder` (software on Pi 5), PyAV outputs | One encode feeds the clip buffer and the live view |
| Live view | MediaMTX | RTSP and WebRTC, re-muxed only, on demand |
| Process management | systemd for vision; Docker Compose for infrastructure | Vision needs direct camera and Hailo access, so it stays on the host |
| Remote access | Tailscale | No inbound ports opened |
| Repo | GitHub | One monorepo |
| Dev environment | Nix flake devShell on the PC; `just` | One task runner across languages |
| Later | Rust via PyO3 and maturin; a public VPS | Rust only if a Python hot path needs it |

## Repository layout and conventions

One GitHub monorepo, `traffic-cam`. Components change together (a detector usually touches a contract, a migration and a dashboard), so one PR, one version and one site config.

```
traffic-cam/
├── flake.nix              # PC devShell: uv, python3, dotnet-sdk, just, mosquitto clients, rerun, mpv
├── justfile               # test, lint, gen-contracts, replay, deploy, logs
├── docs/                  # this plan, ADRs (docs/adr/0001-mqtt-bus.md, ...)
├── contracts/             # JSON Schema for every MQTT payload
├── config/
│   └── site.yaml          # zones, lines, signal heads, crops, detector parameters (no secrets)
├── vision/                # Python (uv project)
│   ├── pyproject.toml
│   ├── src/trafficcam/
│   │   ├── sources/       # PiCameraSource, VideoFileSource
│   │   ├── inference/     # HailoBackend, RecordedBackend, crop planner
│   │   ├── tracking/
│   │   ├── geometry/      # zones, lines, homography
│   │   ├── signals/       # observer, phase estimator
│   │   ├── detectors/     # one module per detector
│   │   ├── passages/      # builds one record per vehicle trip
│   │   ├── video/         # encoder, clip ring buffer, live output
│   │   ├── bus/           # MQTT publisher
│   │   ├── health/        # metrics, image quality, camera alignment, watchdog
│   │   └── debug/         # Rerun sink
│   └── tests/             # unit tests + fixture tests on recorded track logs
├── ingest/                # .NET solution: worker, API, notifier, tests
├── db/migrations/         # plain SQL, applied by ingest at startup
├── deploy/
│   ├── compose.yaml
│   ├── systemd/trafficcam-vision.service
│   ├── grafana/           # provisioning + dashboard JSON
│   ├── mosquitto/
│   └── mediamtx/
└── tools/                 # calibration helpers, replay, labelling export, prototypes/
```

- **Contracts first**: every MQTT payload is defined once in `contracts/` as JSON Schema. `just gen-contracts` generates pydantic models and C# types.
- **One owner per piece of state**: vision only publishes to MQTT; ingest owns the database and its migrations. Migrations are plain SQL (via DbUp), because TimescaleDB DDL fits badly in EF Core.
- **Site config is data**: `config/site.yaml` is validated by a pydantic model at startup. Its content hash is attached to every event and passage.
- **.NET conventions**: `Directory.Build.props`, central package management (`Directory.Packages.props`), xUnit, nullable enabled. Publish from the PC with `-r linux-arm64`, framework-dependent; no emulation needed.
- **Ignore from day one**: `*.mp4`, `*.h264`, `*.jpg`, `*.png`, `*.rrd`, `clips/`, `recordings/`, `.env`. Commit a `.env.example`.

## Contracts: MQTT, database, site config

Three contracts hold the system together: MQTT payloads between vision and ingest, the database schema behind dashboards, and the site config that defines the scene. All three are versioned in the repo.

### MQTT topics

| Topic | QoS | Retained | Payload | Producer → consumers |
| --- | --- | --- | --- | --- |
| `trafficcam/v1/passages` | 1 | No | One completed vehicle trip | vision → ingest |
| `trafficcam/v1/events/{type}` | 1 | No | One detector event | vision → ingest, notifier |
| `trafficcam/v1/signals/{head_id}` | 1 | Yes | Signal state change | vision → ingest, dashboards |
| `trafficcam/v1/clips/{clip_id}` | 1 | No | Clip finished: path, start, end, keyframe | vision → ingest, notifier |
| `trafficcam/v1/cmd/clip` | 1 | No | Manual clip trigger with a reason | you or tools → vision |
| `trafficcam/v1/status/vision` | 1 | Yes | `online` / `offline` via MQTT last will | vision → monitoring |

Per-frame tracks never go on MQTT; they are too chatty. They go to optional local track logs (see Testing). Ingest uses a persistent session, so events queue in Mosquitto while it restarts.

Every payload shares one envelope:

```json
{
  "schema": "event/1",
  "id": "0b8f…",
  "ts": "2026-10-04T13:05:12.345Z",
  "camera": "junction-1",
  "config_hash": "sha256:…",
  "type": "box_junction_stop",
  "detector_version": "1",
  "passage_id": "6c1d…",
  "track_id": 1234,
  "class": "car",
  "confidence": 0.82,
  "attrs": { "stationary_s": 6.2, "zone": "box" },
  "clip_id": null
}
```

Timestamps are UTC, derived from the frame's sensor timestamp, not from when the message was sent.

### Database schema (first cut)

```sql
CREATE TABLE passages (
  id uuid NOT NULL, camera text NOT NULL,
  first_seen timestamptz NOT NULL, last_seen timestamptz NOT NULL,
  track_id int, class text,
  entry_zone text, exit_zone text, movement text,          -- e.g. 'south->east'
  stopline text, stopline_crossed_at timestamptz,
  signal_state_at_crossing text, signal_source text,       -- 'observed' | 'inferred'
  speed_kmh real, flags jsonb, config_hash text,
  PRIMARY KEY (id, first_seen)
);
SELECT create_hypertable('passages', 'first_seen');

CREATE TABLE events (
  id uuid NOT NULL, ts timestamptz NOT NULL, camera text NOT NULL,
  type text NOT NULL, passage_id uuid, confidence real,
  attrs jsonb, clip_id uuid, config_hash text, detector_version text,
  PRIMARY KEY (id, ts)
);
SELECT create_hypertable('events', 'ts');

CREATE TABLE signal_changes (
  ts timestamptz NOT NULL, camera text NOT NULL, head_id text NOT NULL,
  from_state text, to_state text NOT NULL,
  source text NOT NULL, confidence real
);
SELECT create_hypertable('signal_changes', 'ts');

CREATE TABLE clips (
  id uuid PRIMARY KEY, event_id uuid, path text NOT NULL, keyframe_path text,
  started_at timestamptz, ended_at timestamptz, bytes bigint, deleted_at timestamptz
);
```

- **Continuous aggregates**: `passages_5m` (counts by movement and class, median and 85th-percentile speed) and `events_1h` (counts by type). Dashboards and the future VPS export read these, not the raw tables.
- **Retention**: raw rows are small; keep them indefinitely at first. Clip files have their own policy (Video section).
- Timescale requires the partitioning time column in every unique key, hence the composite primary keys.

### Site config (`config/site.yaml`)

All coordinates are pixels in the 2028×1520 frame. Draw polygons on a full frame grab; Roboflow's PolygonZone web tool outputs numpy-ready coordinates.

```yaml
camera: { id: junction-1, size: [2028, 1520], fps: 15 }
inference:
  model: /usr/share/hailo-models/yolov8s_h8.hef
  threshold: 0.3
  classes: [car, truck, bus, motorcycle, bicycle, person]
  crops:                                          # x, y, w, h; square, upscaled to 640x640
    - [600, 500, 700, 700]
    - [200, 900, 620, 620]
zones:
  box_junction: { polygon: [[...], ...] }
  approach_south: { polygon: [[...], ...] }
  exit_east: { polygon: [[...], ...] }
lines:
  stopline_south: { points: [[x1, y1], [x2, y2]], direction: inbound }
movements:
  left_turn_watch: { from: approach_x, to: exit_y }   # the left turn to monitor
signal_heads:
  sh_south_primary:
    lamps: { red: [x, y, w, h], amber: [x, y, w, h], green: [x, y, w, h] }
    controls: [stopline_south]
detectors:
  box_junction: { min_stationary_s: 3.0, exempt_movements: [right turns] }
  red_light: { grace_s: 0.5 }
  speed: { homography: [[...]], limit_mph: 30 }
  incident: { decel_mps2: 6.0, notify_min_confidence: 0.7 }
clips: { dir: /mnt/data/clips, pre_s: 5, post_s: 55, retention_days: 30, max_gb: 200 }
```

Crop sizes and positions above are placeholders; set them from a frame grab so the crops cover the junction and foreground approach with some overlap.

## Vision service design

One Python process, `trafficcam-vision`, runs the whole frame loop at 15 fps and never blocks on I/O. It runs under systemd on the host, because it needs the camera and `/dev/hailo0` directly.

### Camera streams

- **Main stream**: 2028×1520 YUV420. Feeds the H.264 encoder (clips and live view) and the signal lamp regions.
- **Low-res stream**: RGB888, e.g. 1280×960. Inference crops are cut from it. picamera2's `RGB888` is BGR byte order, which is what the Hailo examples and OpenCV expect.
- Every frame carries `SensorTimestamp`, used for speeds and event times.
- **Spike in Phase 5**: confirm the lamps are readable from the main stream's luma (Y) and colour (UV) planes at 2028×1520. Fallback: a third read path at full sensor resolution for the lamp regions only.

### Per-frame loop

1. Take one capture request and read the low-res array and metadata from it, so image and detections are the same frame.
2. Cut the configured crops, resize each to 640×640, run them through Hailo one after another, map boxes back to full-frame coordinates, and merge overlaps with NMS. Keep the configured classes above the threshold.
3. Update the tracker and drop detections whose `tracker_id` is -1 (unconfirmed).
4. Geometry: use each box's bottom-centre as its ground point. Compute zone membership and line crossings, with direction.
5. Update the signal observer and the phase estimator.
6. Update the passage builder. A track lost for longer than a timeout ends its passage, which is then published.
7. Run every detector on a `FrameContext`: time, tracks, zone membership, crossings, signal states.
8. Queue events and passages for MQTT; trigger clips for events that need one.
9. Update health metrics; send `WATCHDOG=1` to systemd; log to Rerun if debug is on.

### Interfaces

Define these as Python `Protocol`s, so hardware stays at the edges and everything else is testable on the PC:

| Interface | Implementations | Used for |
| --- | --- | --- |
| `FrameSource` | `PiCameraSource`, `VideoFileSource` (PyAV, keeps timestamps) | Live camera; replay of recorded clips |
| `InferenceBackend` | `HailoBackend`, `RecordedBackend`; optional `OnnxBackend` | Pi; tests from track logs; PC convenience |
| `Tracker` | `ByteTrackTracker` wrapper | Stable IDs |
| `SignalObserver` | `LampRoiObserver` | Visible heads |
| `PhaseEstimator` | `NullEstimator`, then `StageSequenceEstimator` | Unseen heads |
| `Detector` | One class per detector | Events |
| `EventSink` | `MqttSink`, `JsonlSink` | Production; tests and replay |
| `ClipRecorder` | `RingBufferRecorder`, `NullRecorder` | Clips on the Pi; disabled in tests |
| `DebugSink` | `RerunSink`, `NullSink` | Behind `--debug-rerun` |

### Threads and back-pressure

- The frame loop runs on the main thread. MQTT publishing (paho's own loop), the encoder (picamera2), the clip writer and the `/metrics` HTTP server each run on their own threads.
- Queues between them are bounded. When one is full, drop the item and increment a counter; never stall capture.

### Startup and config

- Load and validate `site.yaml`, compute its hash, and refuse to start on invalid config.
- Wait for NTP time sync before publishing anything. The Pi has no battery-backed clock by default, so timestamps after a power cut are wrong until sync.
- Config changes take effect on restart; `just deploy` restarts the service.

## Signal state and phase estimation

Visible signal heads are read directly and are the only source trusted for red-light events. Heads that can't be seen are inferred, tagged as inferred, and used only for supplementary stats.

### Signal observer (visible heads)

- **Regions**: one small rectangle per lamp (red, amber, green) per head, in main-stream coordinates, set in `site.yaml`. `tools/` gets a helper that grabs a frame and lets you click the lamp positions.
- **Measure**: mean brightness and colour per lamp, compared *between the head's own lamps*, not against fixed thresholds. Low evening sun can make unlit lamps look lit; comparing them against each other copes with that.
- **Flicker**: LED signals on 50 Hz mains can flicker at 100 Hz, so with short exposures some frames show a lit lamp as dark. Take a majority vote over about 5 frames before accepting a change.
- **Sequence**: a state machine enforcing the UK order red → red+amber → green → amber → red. An impossible transition is logged as an anomaly, not accepted.
- **Unknown**: report `unknown` when a lamp is blocked (a bus in front) or confidence is low. Never guess.
- **Output**: a `signal_changes` row and MQTT message on each confirmed change, with `source = observed` and a confidence.
- **Mapping**: each head lists the stop lines or movements it controls (`controls` in `site.yaml`). Work this out by recording several full light cycles before writing detector logic.
- **Validation**: hand-label signal states in recorded clips at day, dusk and night, then compare against the observer's output.

### Phase estimator (heads not visible)

Fixed timers will not work here. City-centre UK junctions are usually demand-responsive: adaptive control systems (SCOOT, MOVA) or detectors that respond to waiting traffic. Phase lengths vary every cycle, and phases are skipped when nobody is waiting, so timers drift within a cycle or two.

The model to build instead:

1. A state machine over the junction's *stage sequence*. The order of stages is fixed even when their durations aren't.
2. Evidence from three sources:
   - the visible heads;
   - mutual exclusion: conflicting movements can never both be green;
   - traffic flow: vehicles streaming across a stop line implies that approach is green.
3. Learn the stage order and typical durations from accumulated `signal_changes` and passage data.
4. Output each inferred state with a confidence and `source = inferred`.

Start with a `NullEstimator` and build this only after weeks of observer data exist (Phase 9). To evaluate it, hide one visible head from the estimator and score its predictions against the observer. If accuracy stays poor, drop the feature; the rest of the system doesn't depend on it.

## Detector specifications

Every detector is a pure function of the frame context and its own state, with parameters in `site.yaml` and fixture tests from recorded track logs. Passages carry the denominators (every crossing and movement), so most detectors only flag exceptions.

| Detector | Fires when | Key parameters | Clip |
| --- | --- | --- | --- |
| Turning movements | Every passage is labelled entry zone → exit zone (stored on the passage, not as an event) | Zone polygons | No |
| Watched left turn | A passage's movement matches `movements.left_turn_watch` | From and to zones | No |
| Box junction stop | Ground point inside the box polygon, speed near zero for ≥ `min_stationary_s` | 3 s to start; speed threshold | Optional |
| Red light | Inbound stop-line crossing while the controlling head is *observed* red for longer than `grace_s` | 0.5 s grace | Optional |
| Amber crossing | Inbound stop-line crossing on amber | — | No |
| Speeding | Median ground speed over a segment exceeds the limit plus a tolerance | Homography; limit; tolerance | No |
| Incident candidate | Weighted score of crash signals above a threshold | Deceleration, overlap, stationary time | Always |
| Near-miss | Post-encroachment time between conflicting tracks below a threshold | e.g. 1.5 s | Optional |

Notes per detector:

- **Box junction**: decide at passage end, so the exit zone is known. Vehicles that turn right are exempt: they may legally wait in the box for oncoming traffic. Record how long the vehicle was stationary and when it started.
- **Red light**: ignore vehicles already past the line when red began. If the signal state is `unknown` or only inferred, record the crossing on the passage but raise no event. Store time-into-red on the event.
- **Speeding**:
  - Speed comes from a homography: a mapping from image pixels to metres on the road surface, calibrated from at least 4 measured ground points (lane widths, the box junction's dimensions, from satellite imagery or a tape measure).
  - Smooth displacement over a window of at least 0.5–1 s using sensor timestamps.
  - Expect ±5–10% accuracy. Report distributions; never single out individual vehicles.
- **Incident candidates**:
  - Signals: abrupt deceleration; two vehicle boxes overlapping then both stopping; a vehicle stationary outside any queue area; a person in a carriageway zone.
  - Trigger a clip every time. Notify only above `notify_min_confidence`, with a cooldown.
  - Expect false positives. Clips are cheap and reviewed by hand.
- **Near-miss**: post-encroachment time is the gap between one vehicle leaving a conflict area and another entering it. It's a standard road-safety measure and occurs daily, so it yields usable data long before a real crash does. Stretch goal, alongside incidents.
- **Class handling**: treat car, truck, bus and motorcycle as "vehicle" for counts, keeping the raw class for breakdowns. Bicycles are separate. A stationary "person" that never moves for minutes is a sign, not a pedestrian: suppress it.

## Video: clips and live view

One software H.264 encode of the main stream feeds both a rolling 5-second buffer for clips and the live view. The Pi 5 has no hardware H.264 encoder, so a second encode is not affordable.

### Encoder

- 2028×1520 at 15 fps, about 6–8 Mbit/s. Measure CPU use; if it's too high, lower the bitrate or frame rate before the resolution.
- picamera2 lets one encoder write to several outputs. Use two:
  1. **Clip buffer**: a circular output holding at least 5 s. Start from the `pyav_circular_capture.py` example in `raspberrypi/picamera2-examples`.
  2. **Live view**: a PyAV output sending MPEG-TS to `udp://127.0.0.1:1234?pkt_size=1316`.
- Clips stay clean, with nothing drawn on them. Overlays exist only in Rerun during development.

### Clips

- On a trigger, write the 5 s pre-roll plus 55 s after to `/mnt/data/clips/YYYY/MM/DD/<clip_id>.mp4`.
- A trigger during an open clip extends it instead of starting a new one, capped at 5 minutes.
- Save a still frame from the event moment as `<clip_id>.jpg`, for emails.
- When the file is closed, publish `trafficcam/v1/clips/<clip_id>` with path, start, end and still-frame path.
- **Manual trigger**: an MQTT message on `trafficcam/v1/cmd/clip`, wrapped as `just clip "reason"`. Use it to test the whole chain.
- **Retention**: a daily job deletes clips older than `retention_days` or, oldest first, when the folder exceeds `max_gb`. It sets `clips.deleted_at`.

### Live view

- MediaMTX path `cam` with `source: udp://127.0.0.1:1234`. It re-muxes without re-encoding, so the cost is near zero.
- Watch in a browser at `http://<pi>:8889/cam` (WebRTC), or in mpv or VLC at `rtsp://<pi>:8554/cam`. Both are reached over Tailscale.
- Run the MediaMTX container with host networking, because UDP input and WebRTC are awkward behind Docker's port mapping.

### Training-data capture (later)

A mode that records 10 minutes every few hours across day, night and rain to the SSD, for fine-tuning a custom model (vans, bicycles, this camera angle).

## Ingest, storage and notifications

A single .NET 10 service, `ingest`, turns MQTT messages into database rows, serves clip files, and sends notifications. It runs as a container in the Compose stack, from Microsoft's stock ASP.NET runtime image with the published build bind-mounted. There is no custom image and no registry.

### Ingest

- **MQTT**: MQTTnet with a fixed client ID, QoS 1 and a persistent session (`CleanStart = false`), so nothing is lost while it restarts.
- **Validation**: payloads are checked against the generated contract types. Invalid messages are logged and counted, never silently dropped.
- **Writes**: batched inserts. QoS 1 can deliver a message twice, so inserts use `ON CONFLICT DO NOTHING` on the primary key.
- **Migrations**: DbUp applies `db/migrations/*.sql` at startup, before consuming.
- **Metrics**: prometheus-net exposes `/metrics` — messages consumed, insert latency, database errors, notifications sent and failed.

### Storage

- PostgreSQL + TimescaleDB in Compose, with its data directory on the SSD (`/mnt/data/postgres`), never on the SD card. Grafana's and VictoriaMetrics' data also live under `/mnt/data`.
- Nightly `pg_dump` to `/mnt/data/backups`, keeping 14 days. Copy one to the PC occasionally.

### Notifier

- A background worker in `ingest`, subscribed to events and clip-ready messages.
- **Rules** in config: which event types notify which channel, a minimum confidence, and a cooldown per type (e.g. one email per 10 minutes). Incidents are the first rule. Red-light runs or anything else can be added later without code changes.
- **Email**: MailKit over authenticated SMTP on port 587 with STARTTLS. Use your mail provider with an app password, or a transactional service (Postmark, Amazon SES). Residential connections usually block port 25.
- **Email content**: sent when the clip is ready, about 60 s after the event. It contains the time, event type, confidence and vehicles involved, the still frame attached as a JPEG, and a link to the clip.
- **No clip attachments**: a 60 s clip is about 45 MB, over most providers' 20–25 MB limit.
- **Optional ntfy push** straight away, with the still frame, for when a one-minute delay is too long.
- **Clip links**: `ingest` serves `/mnt/data/clips` read-only over HTTP, reachable only over Tailscale.
- **Secrets**: SMTP and database credentials live in `deploy/.env`, which is git-ignored. Commit `deploy/.env.example`.

### Public stats (later)

Export only the continuous aggregates (counts and speed distributions by movement and hour) to a separate VPS, pushed over outbound HTTPS. No clips, images, track IDs or anything that identifies a vehicle ever leaves the Pi.

## Health, operations and deployment

The box must recover from anything short of hardware failure without a visit, and tell you when it can't.

### Metrics

- **Vision service** (`/metrics` on port 9200): frame rate and frame-interval p99; inference time per crop; detections and active tracks; dropped queue items; MQTT connection state; time of the last event per type; signal-observer confidence and share of `unknown`; image brightness and sharpness (Laplacian variance); camera alignment offset; clip-buffer state; process memory.
- **Host**: node\_exporter for CPU, memory, disk, network and temperatures. Pi throttling comes from `vcgencmd get_throttled`, written to node\_exporter's textfile collector by a systemd timer.
- **Camera moved**: every 5 minutes, align the current frame's edges against a reference frame saved at calibration. An offset above a few pixels raises an alert, and events are flagged until you recalibrate.

### Alerts (Grafana → email and ntfy)

- Vision down: no metrics scrape, or `status/vision` offline, for 2 minutes.
- Frame rate below 10 fps for 5 minutes.
- No passages for 30 minutes in daytime.
- Camera moved.
- Disk above 85% full.
- CPU above 80 °C, or throttling.
- MQTT or ingest down; clip write failures; email send failures.

### Resilience

- **Vision service**:
  - systemd settings: `Type=notify`, `Restart=always`, `RestartSec=5`, `WatchdogSec=30`, `RequiresMountsFor=/mnt/data`.
  - Ordering: start after `time-sync.target`, with `systemd-time-wait-sync` enabled.
  - Send the watchdog ping from inside the frame loop (`python3-systemd` or `sdnotify`), so a stalled loop gets restarted.
- **Hardware watchdog**: `RuntimeWatchdogSec=15` in `/etc/systemd/system.conf` reboots the Pi on a kernel hang. This changes system config, so ask first.
- **Containers**: `restart: unless-stopped` with health checks.
- **SD card wear**: set journald `SystemMaxUse=200M` now. Later, boot the OS from the SSD (ask first; it changes the boot order).
- **Clock**: the Pi 5's real-time clock has no battery by default. Either add the official RTC battery, or rely on the time-sync wait above.

### Deployment

`just deploy`, run on the PC over Tailscale:

1. rsync `vision/`, `config/` and `deploy/` to `~/trafficcam` on the Pi.
2. `uv sync --frozen` into the venv (created with `--system-site-packages`).
3. Install or refresh the systemd unit, then `systemctl restart trafficcam-vision`.
4. `docker compose pull && docker compose up -d`. Ingest is published on the PC into `deploy/ingest/` (git-ignored) before the rsync, so it travels with `deploy/` and its container is restarted when the build changes.

### Ports

Nothing is forwarded on the router; everything is reached over the LAN or Tailscale.

| Port | Service | Exposure |
| --- | --- | --- |
| 1883 | Mosquitto | localhost and tailnet |
| 3000 | Grafana | tailnet |
| 5432 | PostgreSQL | localhost only |
| 8080 | ingest (clips, API) | tailnet |
| 8428 | VictoriaMetrics | localhost |
| 8554 | MediaMTX RTSP | tailnet |
| 8889 | MediaMTX WebRTC | tailnet |
| 9100 | node\_exporter | localhost |
| 9200 | vision metrics | localhost |
| 9876 | Rerun gRPC (debug only) | LAN, when enabled |

## Testing and development workflow

Most logic is tested on the PC from recorded track logs; only capture and inference need the Pi. Never tune a detector against live traffic alone.

### Three levels of test

1. **Unit tests** (PC): geometry, the signal state machine, detectors fed with made-up tracks.
2. **Fixture tests** (PC): short excerpts of real track logs around known moments, each with an expected-events file. A detector change must keep them passing; regenerate expected output deliberately, never automatically.
3. **Replay on the Pi**: `VideoFileSource` with `HailoBackend` over recorded clips. Compare against hand labels to get precision and recall per detector. `just replay <clip>`.

### Track logs

- `--record-tracks` writes per-frame detections, tracks, zone membership and signal observations to `/mnt/data/tracklogs/`, one file per hour, kept 7 days.
- They let new detector logic be re-run over past days without the video. Copy excerpts of a few seconds into `vision/tests/fixtures/`.

### Video and labels

- Raw clips stay out of git, on the SSD or the PC. The repo holds a manifest: file name, SHA-256, conditions (day, dusk, night, rain) and a labels file per clip.
- Labels are event timestamps written while watching in mpv or Rerun.

### Debugging with Rerun

- `--debug-rerun` serves gRPC on port 9876 with a 512 MiB buffer cap. It logs JPEG frames, boxes with track IDs, zone and line outlines, signal states and events on one timeline.
- `--log-every N` thins the stream if Wi-Fi or CPU struggles.
- The SDK version on the Pi must equal the viewer's (`rerun --version` on the PC).

### PC-only development

- The devShell provides everything the tests need, without the Pi.
- An optional `OnnxBackend` runs the same YOLOv8s on the PC CPU for visual replays. Results differ slightly from the quantised Hailo model, so fixtures and acceptance numbers always come from the Pi.

### Custom model (later)

Collect frames with training-data capture, label vans, bicycles and this camera angle, and train with Ultralytics on the PC. Compile to HEF with Hailo's Dataflow Compiler, which runs on x86 Linux (Docker on NixOS), then validate with replays before switching.

## Privacy and data handling

The camera covers a public street, so the design collects statistics, not identities, and keeps footage short-lived. In the UK, the ICO treats home CCTV that captures beyond your property as subject to UK GDPR. This is not legal advice: read the ICO's domestic CCTV guidance before going live.

- **No identification**: no number-plate reading, no face recognition, no linking of tracks to identities. Track IDs are per-session integers.
- **Short retention**: clips default to 30 days and are deleted automatically. Track logs are kept 7 days. Statistics are kept indefinitely.
- **Access**: footage and dashboards are reachable over Tailscale only.
- **What leaves the Pi**: only incident emails carry an image, and only to your own mailbox. Public stats are aggregates only.
- **Repo hygiene**: no footage or frame grabs in git, including screenshots in `docs/`, especially if the repo might go public.

## Roadmap

Build in this order. Each phase ends with acceptance checks that run on the real hardware. Phases 1–4 deliver a working counter with dashboards before any signal or video work. Acceptance targets are starting points; adjust them once real numbers exist.

### Phase 1 — Repo and dev environment

- [x] Create the `traffic-cam` repo with the layout above, `.gitignore`, `deploy/.env.example` and a README.
- [x] `flake.nix` devShell: uv, Python 3.13, .NET 10 SDK, just, mosquitto clients, rerun 0.37.2, mpv, ffmpeg, jq.
- [x] `justfile`: `test`, `lint`, `fmt`, `gen-contracts`, `deploy`, `replay`, `logs`, `clip`.
- [x] Contracts v1 (passage, event, signal change, clip, status, clip command) with pydantic and C# generation.
- [x] `site.yaml` schema, loader and hash.
- [x] First draft from a full-frame grab: crops, box polygon, foreground stop line, approach and exit zones.
- [x] ADRs: monorepo; MQTT bus; Postgres with TimescaleDB; vision in Python on the host.

Acceptance: `just test` and `just lint` pass on the PC, and the draft config validates.

### Phase 2 — Vision core

- [x] `PiCameraSource` (main 2028×1520 YUV420, low-res 1280×960 RGB888, 15 fps) and `VideoFileSource`.
- [x] `HailoBackend`: crop planner, per-crop inference, merge with NMS. Uses `yolov8s_h8.hef`.
- [x] Tracker wrapper around `ByteTrackTracker`, dropping `tracker_id == -1`.
- [x] Geometry: zones, directional lines, ground point.
- [x] Passage builder.
- [ ] `MqttSink` (QoS 1, last-will status) and `JsonlSink`.
- [ ] `RerunSink` behind `--debug-rerun`; `--record-tracks`.
- [ ] Metrics endpoint, image-quality metrics, systemd unit with watchdog and time-sync wait.
- [ ] `just deploy` working end to end.

Acceptance:

- Runs 24 h on the Pi with no restarts and flat memory use.
- Sustains at least 14 fps.
- Fifty passages spot-checked in Rerun have sensible movements.
- Replaying a recorded clip gives the same passages as the live run, within tolerance.

### Phase 3 — Infrastructure and ingest

- [ ] Install Docker on the Pi from Docker's Debian repository (Trixie).
- [ ] `compose.yaml` services:
  - Mosquitto, with persistence;
  - TimescaleDB;
  - Grafana;
  - VictoriaMetrics, with a scrape config;
  - node\_exporter;
  - MediaMTX, on the host network.

  All data volumes go under `/mnt/data`.
- [ ] `ingest`: MQTT consumer, DbUp migrations, idempotent inserts, `/metrics`. Published on the PC into `deploy/ingest/` and run from the stock ASP.NET runtime image.
- [ ] Grafana provisioning: datasources and dashboards for passages by movement and class, vision health, and host health.
- [ ] Camera-moved check and its alert, plus alerts for vision down, low fps, disk and temperature or throttling.
- [ ] Nightly `pg_dump`.

Acceptance:

- Dashboards show live counts.
- After `sudo reboot`, everything comes back without intervention.
- Stopping `ingest` for 10 minutes loses no passages.

### Phase 4 — First detectors

- [ ] Box junction stop, with the right-turn exemption.
- [ ] Watched left turn, plus a turning-movements dashboard.
- [ ] Fixture tests for both, from real track logs.

Acceptance: on 30 minutes of labelled footage, box-junction precision is at least 0.9 and recall at least 0.8. Movement counts are within 5% of a manual count.

### Phase 5 — Signals and red light

- [ ] Spike: lamp readability from the main stream at day, dusk and night.
- [ ] Lamp-region calibration tool.
- [ ] `LampRoiObserver`: sequence state machine, flicker vote, `unknown` handling.
- [ ] Map heads to stop lines from at least 10 recorded cycles.
- [ ] `signal_changes` dashboard, showing cycle and phase durations.
- [ ] Red-light and amber detectors; signal state recorded on passages.

Acceptance: the observer matches hand labels on at least 98% of observed seconds across day, dusk and night samples. No red-light event is ever raised on an `unknown` or inferred state.

### Phase 6 — Video

- [ ] One encoder with clip-buffer and live outputs; measure CPU.
- [ ] Clips: 5 s before plus 55 s after, extension on overlap, still frame, clip messages, retention job.
- [ ] Manual trigger via `just clip`.
- [ ] MediaMTX live view over Tailscale.

Acceptance:

- A manual trigger yields a playable 60 s clip with at least 5 s of pre-roll.
- Live view plays in a phone browser over Tailscale.
- Total CPU stays under 50%, averaged over an hour.

### Phase 7 — Notifications

- [ ] Notifier: rules config, SMTP email with still frame and clip link, cooldown, optional ntfy.
- [ ] Clip file endpoint on `ingest`, reachable over Tailscale only.

Acceptance: a test incident produces an email within 90 s, with the still frame and a working clip link. A second test within 10 minutes is suppressed by the cooldown.

### Phase 8 — Speed

- [ ] Homography calibration tool using at least 4 measured ground points, saved in `site.yaml`.
- [ ] Speed on passages, speeding detector and speed-distribution dashboard.

Acceptance: a car driven through at a steady, GPS-logged speed reads within ±10%.

### Phase 9 — Advanced detection

- [ ] Incident-candidate detector and near-miss detector.
- [ ] Stage-sequence phase estimator, evaluated against a held-out visible head.
- [ ] Training-data capture; custom model (vans, bicycles) compiled with the Hailo Dataflow Compiler.

Acceptance: incident false candidates stay within a tolerable daily number; estimator accuracy is reported; the custom model beats the stock model on the replay set.

### Phase 10 — Public stats

- [ ] Choose a VPS and front end; push aggregates over outbound HTTPS; nothing that identifies a vehicle.

### Later, ask first

- [ ] Boot the OS from the SSD.
- [ ] Hardware watchdog (`RuntimeWatchdogSec`).
- [ ] RTC battery.

## Still to install

The Pi has the OS, camera stack, Hailo packages and a venv with `rerun-sdk`; everything else below is new.

| Where | What | Needed by | Notes |
| --- | --- | --- | --- |
| Pi | `uv` | Phase 1 | Standalone installer; venv created with `--system-site-packages` |
| Pi | Python packages: `supervision`, `trackers`, `paho-mqtt`, `pydantic`, `pyyaml`, `prometheus-client` | Phase 2 | Through `uv sync` from the repo lockfile; keep `rerun-sdk==0.37.2` |
| Pi | PyAV (`av`) | Phase 2 | Check `python3 -c 'import av'` first; picamera2's PyAV outputs need it |
| Pi | `python3-systemd` (apt) | Phase 2 | Watchdog notifications from the frame loop |
| Pi | Tailscale | Phase 2 | Skip if already on the tailnet |
| Pi | Docker Engine and Compose plugin | Phase 3 | Docker's Debian apt repository for Trixie |
| Pi | Containers: Mosquitto, TimescaleDB, Grafana, VictoriaMetrics, node\_exporter, MediaMTX, `ingest` | Phase 3 | Pinned tags in `compose.yaml`; arm64 images |
| PC | devShell from `flake.nix` | Phase 1 | uv, Python 3.13, .NET 10 SDK, just, mosquitto clients, rerun 0.37.2, mpv, ffmpeg, jq |
| PC | Docker or Podman | Phase 3 | Optional: run the Compose stack locally for ingest tests |
| Account | SMTP with an app password, or a transactional mail service | Phase 7 | Secrets in `deploy/.env` |
| Phone | ntfy app | Phase 7 | Optional instant alerts |
| PC | Hailo Dataflow Compiler (Docker, x86) | Phase 9 | Only for a custom model |

Checks to run before Phase 2:

- `ls /usr/share/hailo-models/`: is a Hailo-8 build of YOLOv8s (`_h8`) present?
- `lsblk -d -o NAME,ROTA,MODEL`: is the drive an SSD (0) or a spinning disk (1)?
- `lsblk --discard /dev/sda`: does the enclosure pass TRIM through (non-zero `DISC-GRAN`)?
- Reboot once and confirm `/mnt/data` mounts by itself.

## Known gotchas

Each of these has already cost time on this hardware or is documented upstream; check this list before debugging.

| Gotcha | What to do |
| --- | --- |
| The Pi 5 has no hardware H.264 encoder or decoder (HEVC decode only) | Encode once, in software. Never add a second encode |
| `rpicam-vid` defaults to 640×480 | Always pass `--width` and `--height` |
| Pi 5 software encoding adds latency | Use `--low-latency` when streaming; it also drops B-frames |
| Raspberry Pi OS Lite has no PyQt5, so picamera2's Qt preview crashes | Never call `start_preview` |
| picamera2's examples moved | They live in `raspberrypi/picamera2-examples`, not the `picamera2` repo |
| Default `yolov8s_h8l.hef` is a Hailo-8L build | Use an `_h8` build on the Hailo-8 when available |
| `hailo-all` (AI HAT+) and `hailo-h10-all` (AI HAT+ 2) can't coexist; driver and runtime versions must match | Never mix in Hailo's own installers; hold package versions |
| picamera2's `RGB888` is BGR byte order | Fine for OpenCV and the Hailo examples; tell Rerun `color_model="BGR"`, or encode JPEG with OpenCV |
| `--roi` (ScalerCrop) crops every stream, the encoder included | Crop in software for inference |
| Squashing the 4:3 frame into 640×640 misses small vehicles | Square crops of the junction |
| `trackers.ByteTrackTracker` gives unconfirmed tracks ID -1, and `LineZone` treats every -1 as one object | Filter `tracker_id == -1` first ([supervision #2578](https://github.com/roboflow/supervision/issues/2578)) |
| `sv.ByteTrack` is deprecated | Use the `trackers` package |
| Rerun SDK and viewer versions must match | Pin `rerun-sdk` to `rerun --version`; re-check after `nix flake update` |
| `rr.serve_grpc()` returns immediately and dies with the process | Keep the process alive; always set `server_memory_limit` |
| Raspberry Pi OS refuses system-wide pip | venv with `--system-site-packages` |
| The USB drive uses `usb-storage`, not UAS | Fine for this load; TRIM may not pass through |
| NixOS blocks inbound connections | The Pi serves; the PC connects (Rerun, video) |
| Recent VLC won't play raw H.264 streams | Send MPEG-TS, or use mpv or ffplay |
| The Pi 5's clock has no battery by default | Wait for time sync before stamping events |
| COCO has no van class; signs get detected as people; "traffic light" means the housing only | Vehicle class grouping; suppress static "people"; lamp logic for state |
| LED signals can flicker at 100 Hz; low sun lights unlit lamps | Multi-frame vote; compare lamps within a head |
| There is one Hailo device | Run inference from one thread; keep any slicer single-threaded |

## Open questions

None of these block Phase 1; each blocks the phase noted.

- [ ] Exact lens model and focal length (Phase 2 crops).
- [ ] Is the official 27 W supply in use? (Phase 2)
- [ ] Is the Toshiba drive an SSD or a spinning disk? (Phase 3 TRIM and power)
- [ ] Has the Pi joined the tailnet, and under what name? (Phase 2 deploy)
- [ ] Which exit is the left turn to watch? (Phase 4)
- [ ] Which signal heads control which stop lines and movements? Record cycles to find out. (Phase 5)
- [ ] The junction's speed limit; speeds are stored in km/h, and the limit and dashboards use mph. (Phase 8)
- [ ] Clip retention: are 30 days and a 200 GB cap right? (Phase 6)
- [ ] Track-log retention: are 7 days enough? (Phase 2)
- [ ] Is the GitHub repo private or public? It affects repo hygiene. (Phase 1)
- [ ] Which mail provider sends the alerts? (Phase 7)
