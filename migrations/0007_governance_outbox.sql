-- Additive: retained across application rollback; never discard undelivered audit events.
CREATE TABLE IF NOT EXISTS gcor.governance_outbox (
    event_id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    response JSONB NOT NULL,
    idempotency_key TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    UNIQUE(bucket, object_key)
);
CREATE INDEX IF NOT EXISTS governance_outbox_pending_idx
    ON gcor.governance_outbox(next_attempt_at, created_at) WHERE published_at IS NULL;
