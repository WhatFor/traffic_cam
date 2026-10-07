-- What a person has said about a clip on the clips site. Ingest never writes these.
ALTER TABLE clips
    ADD COLUMN viewed_at timestamptz,
    ADD COLUMN archived_at timestamptz,
    -- Vision is told too, and keeps such a clip's files longer.
    ADD COLUMN false_positive_at timestamptz,
    ADD COLUMN description text;
