ALTER TABLE gcor.ingestion_records ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE gcor.ingestion_records ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE gcor.ingestion_records ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE gcor.ingestion_records DROP CONSTRAINT IF EXISTS ingestion_records_status_check;
ALTER TABLE gcor.ingestion_records ADD CONSTRAINT ingestion_records_status_check
    CHECK (status IN ('archived', 'indexed', 'failed', 'quarantined', 'backfilled'));

CREATE TABLE IF NOT EXISTS gcor.event_projection (
    event_id CHAR(64) PRIMARY KEY,
    event_kind INTEGER NOT NULL,
    channel_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('processing', 'indexed', 'failed', 'skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    document_id UUID REFERENCES gcor.documents(id) ON DELETE SET NULL,
    record_id UUID REFERENCES gcor.ingestion_records(id) ON DELETE SET NULL,
    error TEXT,
    event_created_at TIMESTAMPTZ NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS event_projection_retry_idx
    ON gcor.event_projection (status, updated_at)
    WHERE status IN ('processing', 'failed');
