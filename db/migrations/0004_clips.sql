-- One row per clip file; the contract's clip/1. Few rows, so a plain table.
CREATE TABLE clips (
    id uuid PRIMARY KEY,
    camera text NOT NULL,
    -- The first trigger's event; every event the clip shows has this clip's id in events.clip_id.
    event_id uuid,
    -- What the clip was recorded for: [{type, at, event_id, reason}], in the order it happened.
    triggers jsonb NOT NULL,
    path text NOT NULL,
    keyframe_path text,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    closed_at timestamptz NOT NULL,
    bytes bigint NOT NULL,
    config_hash text,
    -- Set when retention removes the files.
    deleted_at timestamptz
);

CREATE INDEX clips_started_at ON clips (started_at DESC);
