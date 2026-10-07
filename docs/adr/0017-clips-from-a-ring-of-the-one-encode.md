# 0017. Clips: cut from a ring of the one encode

Date: 2026-10-06. Status: Accepted.

## Context

An event is easier to judge with video of it. The Pi 5 encodes H.264 in software, so there is one encode, of the main stream, and it already feeds the live view. The build plan has clips come from the same encode through a 5-second circular buffer, written 5 s before and 55 s after a trigger.

Two things about this system do not fit that:

- **Events are raised late.** Detectors decide when a passage closes (0013, 0014, 0016). On the evening of 2026-10-06 that was a median of 4 to 19 s after the moment the event describes, depending on its type, and up to 65 s. A 5-second buffer would have missed the moment almost every time.
- **picamera2 calls each of an encoder's outputs on the encode path, holding a lock.** The data drive takes 0.2 to 0.9 s for some writes (0003). An output that writes a file there can stall the encoder.

## Decision

**A ring of encoded video in memory.** The encoder has a second output that does nothing but append each packet to a ring holding the last `buffer_s` (90 s, about 70 MB at 6 Mbit/s). Nothing on the encode path touches the disk.

**A clip is a window of time around the moment, not around when it was asked for.** A trigger at time `at` wants `[at - pre_s, at + post_s]`. A worker thread copies packets from the ring into the file, starting at the keyframe at or before the window's start, and goes on as new packets arrive until the window's end has passed. Keyframes are a second apart, so a clip has up to a second more lead-in than asked for. The video is copied, not encoded again.

**Lengths are per event type.** `clips.events` in `site.yaml` lists the event types that trigger a clip, each with its own `pre_s` and `post_s`; `red_light` and `banned_turn` have 5 s and 15 s. `box_junction_stop` has 30 s and 15 s: a stop in the box can be the aftermath of a collision, and on 2026-10-07 a clip with 5 s of lead-in began a moment after one (0020). The top-level `pre_s` and `post_s` (5 s and 55 s) are the default, used by a type that gives none and by the manual trigger. `amber_crossing` is not listed: at 20 an hour it would record a third of the time. A type may also give `min`, a map of event attribute to the least value that earns a clip: `speeding` is listed with `min: { speed_mph: 45 }`, so the event at 35 mph gets no clip and one at 45 does (added with 0019). `max` is the same the other way: `near_miss` has `max: { pet_s: 1.5 }` (0020). `incident_candidate` is listed with the default lengths.

**Triggers that overlap share a clip.** A trigger joins an open clip if its own window starts inside it; the clip's end moves out to cover it, up to `max_s` (300 s) in all. Otherwise it gets a clip of its own, so a trigger never loses its lead-in by joining. Once a clip's end has passed it is closed and cannot be joined. An event is published with its clip's id; the clip does not exist yet at that point.

**What a clip is of is recorded three times**, from one `Clip` record:

- the record's `triggers` list: for each, its type (an event type, or `manual`), when it happened, the event's id, and the reason given with a manual trigger;
- a `.json` file beside the `.mp4`, holding the record, so a copied clip still says what it shows;
- the `clips` table, with `triggers` as `jsonb`; and each event row has its `clip_id`.

Where in the clip something happens is its time less the clip's `started_at`.

**Files.** `<clips.dir>/YYYY/MM/DD/<clip id>.mp4`, written as `.mp4.part` and renamed when complete, with the index at the front so it plays over HTTP. A `.jpg` beside it is the frame at the first trigger's moment, at full size, decoded from the finished clip.

**The manual trigger** is a `ClipCommand` on `trafficcam/v1/cmd/clip`. Vision takes the time it arrives as the moment. `just clip --host <pi> "reason"` sends one and waits for the clip to be announced. Vision's broker session is not kept, so a command sent while it is down is never acted on. A command may give its own `pre_s` and `post_s`, held to what the ring holds and to `max_s`; the clips site's quick clip asks for 90 s before and none after (0021).

**Retention is vision's job**, since it owns the files: at start, after each clip, and daily, it deletes clips older than `retention_days` (30), then the oldest until the folder is under `max_gb` (200). Only files named as clips are touched. Each deletion is published as a `ClipDeleted` record and ingest sets `clips.deleted_at`; the row stays. A clip with a `.keep` file beside it is deleted only after `kept_days` (183) and never to make room (0021).

**picamera2's `CircularOutput2` is not used.** It is a delay line: everything comes out `buffer` seconds late, so a clip would be ready 90 s after it ended, and it writes the file on the encode path.

## Consequences

- A clip is ready about 2 s after its window ends, or straight away if the event was raised after that. A manual clip is ready a minute after it is asked for.
- Measured on the Pi on 2026-10-06 while a 61 s, 49 MB clip was written and closed: 900 frames a minute, and no frame interval over 100 ms.
- A trigger about a moment older than the ring gets a clip that starts after the moment, or no clip at all if the whole window has gone. The second case is counted as a failed clip.
- A clip that cannot be written is dropped: its event still carries a `clip_id` that nothing answers to. Failures are counted in `trafficcam_vision_dropped_total{queue="clips"}` and raise an alert (0012). The clips folder is never created by vision, so with the data drive unmounted nothing is written to the SD card.
- On shutdown an open clip is closed with what it has and announced. If vision is killed instead, the `.part` file is removed at the next start.
- A clip holds the camera's whole view for its length: every vehicle and pedestrian in it, not only the one the event is about. Clips stay on the Pi.
- Clips are served by the clips site (0018). The path in the record is a path on the Pi.
- If the encoder's bitrate is raised, the ring grows with it.
