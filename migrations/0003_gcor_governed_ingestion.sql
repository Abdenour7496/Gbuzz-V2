ALTER TABLE gcor.documents DROP CONSTRAINT IF EXISTS documents_content_sha256_key;

ALTER TABLE gcor.documents ADD COLUMN IF NOT EXISTS identity_sha256 CHAR(64);

UPDATE gcor.documents
SET identity_sha256 = encode(digest(
    content_sha256 || chr(31) || access_level || chr(31) || COALESCE(agent_id, '') || chr(31) ||
    COALESCE(metadata->>'channel_id', '') || chr(31) || COALESCE(metadata->>'channel_name', ''),
    'sha256'
), 'hex')
WHERE identity_sha256 IS NULL;

ALTER TABLE gcor.documents ALTER COLUMN identity_sha256 SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS documents_identity_sha256_idx ON gcor.documents (identity_sha256);
CREATE INDEX IF NOT EXISTS documents_content_sha256_idx ON gcor.documents (content_sha256);

CREATE TABLE IF NOT EXISTS gcor.ingestion_records (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES gcor.documents(id) ON DELETE CASCADE,
    content_sha256 CHAR(64) NOT NULL,
    bucket TEXT NOT NULL,
    original_key TEXT NOT NULL,
    markdown_key TEXT NOT NULL,
    record_key TEXT NOT NULL,
    channel_id TEXT,
    channel_name TEXT,
    event_id TEXT,
    event_kind TEXT,
    source_uri TEXT,
    status TEXT NOT NULL CHECK (status IN ('archived', 'indexed', 'failed', 'quarantined')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bucket, record_key)
);

CREATE INDEX IF NOT EXISTS ingestion_records_document_idx ON gcor.ingestion_records (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_records_event_idx ON gcor.ingestion_records (channel_id, event_id);
