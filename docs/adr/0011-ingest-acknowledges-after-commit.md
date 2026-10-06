# 0011. Ingest acknowledges a message only after it is stored

Date: 2026-10-05. Status: Accepted.

## Context

A passage exists in one place until it is in the database: first in vision's publish queue, then in the broker. Ingest can be stopped, restarted or cut off from the database at any time, and the plan's acceptance is that stopping it for ten minutes loses nothing. QoS 1 and a persistent session (0001) only help if ingest does not tell the broker "received" before the row is safe.

## Decision

Ingest acknowledges each message by hand, after the database transaction that holds it has committed.

- **Session**: MQTT v5, client id `trafficcam-ingest`, `CleanStart = false` and a session that never expires. In v5 a session ends at disconnect unless an expiry is set, whatever `CleanStart` says.
- **Batches**: the first message opens a one-second window; everything received in it is inserted in one transaction, because a commit on the data drive takes 0.2 to 0.9 seconds (0003). The broker sends at most 20 unacknowledged messages at a time, so that is the largest batch.
- **Duplicates**: inserts are `ON CONFLICT (id, first_seen) DO NOTHING`. Vision derives a passage's id from its camera, first-seen time and track id, so a redelivered passage is the same row.
- **Invalid messages**: a payload that does not parse as the record its topic carries is logged with its content, counted, and acknowledged. Unacknowledged, it would come back for ever.
- **Events** (added 2026-10-06) are handled exactly as passages are: one subscription to `events/+`, the same batches and transaction, and `ON CONFLICT (id, ts) DO NOTHING`. A batch can hold both kinds.
- **Signal changes** (added 2026-10-06) likewise: one subscription to `signals/+`, into `signal_changes`. They are published retained, so ingest receives each head's latest change again whenever it subscribes; that is a duplicate and stores nothing.
- **Database down**: the batch is kept and retried with a growing delay, up to 30 seconds. Nothing is acknowledged meanwhile, so the broker holds everything else, up to its queue of 100,000 messages.
- **Reconnects**: a message that arrived on an earlier connection is stored but not acknowledged on the new one; the broker sends it again and it counts as a duplicate.

## Consequences

- A crash between commit and acknowledgement stores a passage and then receives it again. That shows as a duplicate in the metrics, never as a second row.
- The database holds passages from the moment ingest first connected. The broker kept nothing for a client it had not seen; earlier passages exist only in track logs (0008).
- An invalid message is gone from the bus once logged. The log line is the only copy.
- With the database down for longer than the broker's queue lasts, the oldest passages are dropped by the broker, not by ingest.
- Catching up after an outage is limited to 20 messages per commit: about a minute for 1,000 passages. Raising Mosquitto's `max_inflight_messages` would speed that up.
- Ingest reports `stored`, `duplicate` and `invalid` counts for each kind of record, insert time, database errors and its connection state at `/metrics`.
