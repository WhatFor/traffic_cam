-- One row per change of a signal head's state; the contract's signal_change/1.
CREATE TABLE signal_changes (
    id uuid NOT NULL,
    ts timestamptz NOT NULL,
    camera text NOT NULL,
    head_id text NOT NULL,
    from_state text,
    to_state text NOT NULL,
    source text NOT NULL,
    confidence real,
    config_hash text,
    PRIMARY KEY (id, ts)
);

SELECT create_hypertable('signal_changes', by_range('ts'));

-- Dashboards read one head's changes in time order.
CREATE INDEX signal_changes_head_ts ON signal_changes (head_id, ts DESC);
