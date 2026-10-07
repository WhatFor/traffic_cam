-- The state of each group of signal heads that change together: the contract's group_state/1.
-- Each group is told twice. Rows with `settled` are the state as known some time on, in
-- order and dated when it happened: the record. The others are the state as known at once.
CREATE TABLE group_states (
    id uuid NOT NULL,
    ts timestamptz NOT NULL,
    camera text NOT NULL,
    group_id text NOT NULL,
    state text NOT NULL,
    -- 'observed' or 'inferred'; null while the state is unknown.
    source text,
    settled boolean NOT NULL,
    config_hash text,
    PRIMARY KEY (id, ts)
);

SELECT create_hypertable('group_states', by_range('ts'));

CREATE INDEX group_states_group_ts ON group_states (group_id, settled, ts DESC);
