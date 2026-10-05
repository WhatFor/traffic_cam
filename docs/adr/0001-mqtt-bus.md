# 0001. MQTT bus between vision and ingest

Date: 2026-10-04. Status: Accepted.

## Context

Vision (Python, on the host) produces passages, events, signal changes and clip notices. Ingest (.NET) stores them and sends notifications. The two must be able to restart independently without losing data, and each piece of state should have one owner: vision never touches the database.

## Decision

Vision publishes to a Mosquitto broker and ingest consumes from it. Topics live under `trafficcam/v1/`.

- QoS 1 throughout. Ingest uses a fixed client id and a persistent session, so messages queue in the broker while it is down.
- Vision's status and the latest state of each signal head are retained. Vision publishes `online` on every connect and registers `offline` as its last will, so the broker announces a crash or power cut; on a clean stop vision publishes `offline` itself.
- Vision publishes only when running from the live camera. A replay of a recorded clip never reaches the broker.
- Frames and per-frame tracks never go on the bus. Tracks are too chatty and go to local track logs.
- The broker requires a password. There is one shared account and no ACLs.

## Consequences

- QoS 1 can deliver a message twice, so ingest's inserts must be idempotent.
- The broker is state: it uses SQLite persistence on the data drive, with the per-client queue raised to 100,000 messages. In a local test, 1,500 queued messages survived a hard kill of the broker.
- Every client needs the password, and any client can read or publish any topic. Per-client accounts with ACLs can be added without changing the topics.
- Payloads need a shared definition in two languages; see 0007.
- Publishing must never stall the frame loop. The MQTT client does its network I/O on its own thread and queues up to 10,000 messages while the broker is unreachable; beyond that, records are dropped and counted.
- Vision publishes passages and its status. Ingest consumes passages; how it does so without losing any is in 0011.
