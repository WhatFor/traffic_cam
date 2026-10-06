-- One row per detector event; the contract's event/1.
CREATE TABLE events (
    id uuid NOT NULL,
    ts timestamptz NOT NULL,
    camera text NOT NULL,
    type text NOT NULL,
    passage_id uuid,
    track_id int,
    class text,
    confidence real,
    attrs jsonb,
    clip_id uuid,
    config_hash text,
    detector_version text,
    PRIMARY KEY (id, ts)
);

SELECT create_hypertable('events', by_range('ts'));
