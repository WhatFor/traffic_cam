# 0012. Alerts: Grafana rules from the repo, sent by email

Date: 2026-10-05. Status: Accepted.

## Context

The health metrics are collected and shown (0009, 0010), but someone has to be looking. The box is meant to say when it needs attention.

## Decision

Grafana evaluates the alert rules and sends the notifications. Rules, the contact point and the notification policy are YAML files in `deploy/grafana/provisioning/alerting/`, provisioned like the dashboards (0010), so they cannot be edited in the UI.

| Alert | Fires when | For |
| --- | --- | --- |
| Vision down | no metrics scrape from vision | 2 min |
| Low frame rate | below 10 frames a second | 5 min |
| Disk filling | `/` or `/mnt/data` more than 85% used | 10 min |
| CPU hot | CPU above 85 C | 5 min |
| Pi throttled | the firmware reports under-voltage or throttling now | 5 min |
| Ingest down | no metrics scrape from ingest | 2 min |
| Ingest database errors | ingest keeps failing to write | 5 min |
| MQTT disconnected | vision or ingest has no broker connection | 2 min |

- **Temperature at 85 C, not the build plan's 80 C.** The Pi runs at 80 to 83 C as it is, so a rule at 80 would never clear. 85 C is where the firmware throttles hard, and the throttling alert covers the soft limit when it lasts.
- **One failure, one email.** The rules about a service's behaviour stay quiet when the service is down; its "down" alert covers that.
- **Email** through the mail provider's SMTP submission, with STARTTLS required. Host, account, token and recipient are in `deploy/.env`. An alert is sent when it fires and when it clears, and again every 24 hours while it lasts.
- **Throttling flags** are not in node-exporter. A small host service, `trafficcam-throttled`, runs `vcgencmd get_throttled` every 30 seconds and writes the flags as `rpi_throttled` gauges to `/dev/shm/trafficcam/`, which node-exporter reads with its textfile collector.

## Consequences

- If Grafana or VictoriaMetrics is down, nothing alerts. Nothing outside the Pi watches the Pi.
- If mail cannot be sent, alerts still show in Grafana but nobody is told.
- The throttling file is in memory, so it costs the SD card nothing and is gone after a reboot until the service writes it again. It lives in `/dev/shm` because the node-exporter container runs unprivileged and cannot read the user's runtime directory.
- If the throttling service stops, its file goes stale and the alert stays quiet. If node-exporter stops, "Disk filling" reports missing data; the other host rules stay quiet.
- The build plan names a systemd timer for the throttling flags. A long-running service does the same without two journal lines every 30 seconds.
- Not alerted on yet: the camera having moved, no passages in daytime, clip-write and mail failures.
