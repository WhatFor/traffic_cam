CREATE EXTENSION IF NOT EXISTS timescaledb;

-- One row per road user's trip through the junction; the contract's passage/1.
CREATE TABLE passages (
    id uuid NOT NULL,
    camera text NOT NULL,
    first_seen timestamptz NOT NULL,
    last_seen timestamptz NOT NULL,
    track_id int,
    class text,
    entry_zone text,
    exit_zone text,
    movement text,
    stopline text,
    stopline_crossed_at timestamptz,
    signal_state_at_crossing text,
    signal_source text,
    speed_kmh real,
    flags jsonb,
    config_hash text,
    -- TimescaleDB needs the partitioning column in every unique key.
    PRIMARY KEY (id, first_seen)
);

SELECT create_hypertable('passages', by_range('first_seen'));
