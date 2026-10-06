# 0009. Vision health: metrics endpoint and watchdog

Date: 2026-10-05. Status: Accepted.

## Context

Vision has to run unattended. Two things were missing: a way to see from outside how it is doing (frame rate, inference time, drops, connection to the broker, image quality), and a way to recover when the frame loop stalls while the process stays alive, which `Restart=always` alone does not catch.

## Decision

**Metrics.** With `--metrics-port`, vision serves Prometheus metrics on localhost, which VictoriaMetrics scrapes. The service uses port 9200.

- One class, `trafficcam.health.metrics.Metrics`, owns every metric. It observes frame results and receives passages like any other output, so the pipeline and the sinks know nothing about Prometheus.
- Counts that other parts already keep (dropped records, broker connection) are read from them when a scrape happens.
- Names start with `trafficcam_vision_`. Process memory and CPU come from the client library's process collector.
- Brightness and sharpness are measured on the low-res frame every 10 seconds. Sharpness is the variance of the Laplacian.

**Watchdog.** The unit is `Type=notify` with `WatchdogSec=30`.

- Vision sends `READY=1` once its arguments and config are valid, then a watchdog ping from the frame loop, after every other output has had the frame, three times per timeout.
- While it waits for the clock to synchronise (0004) it keeps pinging: that wait can be long and is not a fault.
- If the pings stop, systemd kills the process and restarts it after 5 seconds.

## Consequences

- An invalid config now fails `systemctl restart`, and with it `just deploy`, where before the start appeared to succeed.
- A watchdog kill is not a clean stop: the broker publishes vision's `offline` last will, and the track log's last second may be lost.
- The watchdog only proves that frames are moving through the loop. A camera that delivers frames of nothing is not caught by it; that is what the image metrics and, later, alerts are for.
- Sharpness depends on the scene, so it differs between day and night. It is useful for spotting a step change, not as an absolute value.
- A port that is already in use stops vision from starting.
- Clips (added 2026-10-06, 0017): clips written, clips deleted and the size of the clips folder. A clip that could not be written counts as a dropped record, under `queue="clips"`.
- Not measured yet: signal-observer confidence, camera alignment. Inference time is per frame, which equals per crop while there is one crop.
- Dashboards and alert rules are separate work.
