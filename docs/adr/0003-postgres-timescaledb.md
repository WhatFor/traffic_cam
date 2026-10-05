# 0003. PostgreSQL with TimescaleDB for passages and events

Date: 2026-10-04. Status: Accepted.

## Context

Every passage, event and signal change is stored as a time series, so that violations can be expressed as rates and shown on custom dashboards, Grafana first. Dashboards, and later a public stats export, should read rollups instead of raw rows.

## Decision

Passages, events and signal changes are stored in PostgreSQL with the TimescaleDB extension: one hypertable per kind, with continuous aggregates for the rollups. Clips get an ordinary table.

- Ingest owns the database and applies migrations at startup. Migrations are plain SQL, because TimescaleDB DDL fits badly in EF Core.
- Service health metrics do not go here. They go to VictoriaMetrics.

## Consequences

- TimescaleDB requires the partitioning time column in every unique key, so primary keys are composite, for example `(id, first_seen)`.
- The data directory is on the external drive, never the SD card. That drive turned out to be a USB hard disk whose synchronous writes take roughly 0.2 to 0.9 seconds each, so ingest must batch its inserts; a commit per message would not keep up.
- Raw rows are small and kept indefinitely for now. Retention applies to clip files, not to the database.
- Migrations are SQL files in `db/migrations/`, embedded in ingest and applied by DbUp at startup. The first creates the `passages` hypertable; the other tables come when vision produces their records.
- Tests run the real migrations against a throwaway PostgreSQL from the devShell, the same PostgreSQL and TimescaleDB versions as the Pi. That is the Apache-licensed TimescaleDB build, which has hypertables but not continuous aggregates; testing those on the PC will need the unfree build.
