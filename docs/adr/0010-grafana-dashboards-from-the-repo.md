# 0010. Grafana dashboards come from the repo

Date: 2026-10-05. Status: Accepted.

## Context

Grafana keeps dashboards in its own database on the data drive. A dashboard built there by hand is not reviewed, not versioned, and is lost with the drive.

## Decision

Dashboards are JSON files in `deploy/grafana/dashboards/`, loaded by a file provider in `deploy/grafana/provisioning/dashboards/`. `just deploy` copies them to the Pi and Grafana picks up changes within 30 seconds.

- The provider has `allowUiUpdates: false`. A dashboard can be changed in the UI to try something, but not saved there.
- To keep a change: export the dashboard's JSON from the UI into its file in the repo, and deploy.
- Each dashboard has a fixed `uid`, so its address stays the same: `/d/system` for the first one.
- Queries name the datasource by its provisioned uid, `victoriametrics`.

The first dashboard, System, shows vision's metrics (0009) and a row of host metrics from node-exporter. The build plan lists vision health and host health as two dashboards; they are one until there is enough on it to split.

## Consequences

- A dashboard edited in the UI and not exported is lost on the next reload of the page.
- Removing a file from the repo removes the dashboard from Grafana.
- The System dashboard has no throttling flags: node-exporter does not have them until the `vcgencmd` textfile collector exists. CPU frequency, which drops when the Pi throttles, stands in.
- Alert rules are not provisioned yet.
