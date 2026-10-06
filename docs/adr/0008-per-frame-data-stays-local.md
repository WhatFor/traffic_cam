# 0008. Per-frame data stays on the Pi: track logs and the Rerun view

Date: 2026-10-05. Status: Accepted.

## Context

Vision produces detections and tracks for every frame, 15 times a second. That is too much for the bus (0001), but it is the raw material for two things: seeing what the pipeline is doing, and re-running tracking, geometry and detectors over past traffic without the video or the accelerator.

## Decision

Per-frame data has two outlets, both local to the Pi and both optional.

**Track logs** (`--record-tracks DIR`, on in the service) are the durable one.

- JSON lines, one file per UTC hour, named like `2026-10-05T21.jsonl`. Each file starts with a header line (schema name, camera, config hash) and gets another whenever the service restarts within the hour. Every other line is one frame: its index and timestamp, the detections, the tracks with the zones each is in, line crossings, and the state of every signal head (0015).
- The format belongs to vision alone. It is defined by pydantic models in `trafficcam.tracklog`, not in `contracts/`, because nothing outside vision reads it.
- Numbers are written so that they read back as the same 32-bit floats. A replay gives the tracker exactly the detections the live run had.
- Files are plain text, appended to and not compressed. Files more than seven days old are deleted when a new hour's file is opened.
- `--tracks FILE...` replays logs: the recorded detections go through the tracker, geometry and passage builder again, with the current `site.yaml`. Like a video replay, it never publishes to the broker.

**The Rerun view** (`--debug-rerun`) is the live one. It is off by default. On the Pi it is switched on by adding `VISION_ARGS=--debug-rerun` to `deploy/.env` and deploying.

## Consequences

- Writing a track log must never slow or stop vision. The frame loop only queues each frame's result, up to a minute's worth; a thread writes the file. If the queue fills, or the directory is missing, or a write fails, frames are dropped and counted, and the writer tries again every minute.
- The writer does not create its directory; `just deploy` does. With the data drive unmounted, nothing is written to the SD card, and recording starts by itself once the drive is there.
- Track logs hold positions and times of vehicles but no images. Short excerpts can be committed as test fixtures.
- A replay uses the recorded detections and signal states only; a log has no image to read lamps from, so the lamp observer's own settings cannot be replayed. Recorded tracks, zones and crossings are there to compare against. Track ids in a replay match the live run only from the start of a run; after a header in the middle of the input they differ.
- The tracker's arithmetic is not guaranteed to be identical on the Pi and the PC, so a replay on the PC can differ from the live run in rare borderline cases.
- With Rerun off there is no picture of what the pipeline sees. The video stream (MediaMTX) is unaffected.
- The retention period, queue size and directory are constants and a command-line argument, not `site.yaml` settings.
