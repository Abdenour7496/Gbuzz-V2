-- Additive operational state; preserve across application rollback.
CREATE TABLE IF NOT EXISTS gcor.ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_id UUID,
    lease_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(actor, request_id)
);
CREATE INDEX IF NOT EXISTS ingestion_jobs_queue_idx ON gcor.ingestion_jobs(next_attempt_at,created_at)
    WHERE status IN ('pending','processing');
CREATE INDEX IF NOT EXISTS ingestion_jobs_channel_idx ON gcor.ingestion_jobs(channel_id,created_at DESC);
CREATE TABLE IF NOT EXISTS gcor.knowledge_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES gcor.documents(id),
    channel_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('incorrect','outdated','missing','helpful')),
    note TEXT NOT NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(actor,request_id)
);
CREATE INDEX IF NOT EXISTS knowledge_feedback_document_idx ON gcor.knowledge_feedback(document_id,created_at DESC);
CREATE TABLE IF NOT EXISTS gcor.worker_heartbeats (
    worker TEXT PRIMARY KEY,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
