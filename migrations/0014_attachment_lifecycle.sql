ALTER TABLE gcor.event_projection
    ADD COLUMN IF NOT EXISTS extraction_version TEXT;

CREATE INDEX IF NOT EXISTS event_projection_extraction_version_idx
    ON gcor.event_projection (extraction_version);

COMMENT ON COLUMN gcor.event_projection.extraction_version IS
    'Version of the deterministic attachment extraction contract last applied.';
