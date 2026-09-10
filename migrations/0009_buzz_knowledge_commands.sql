-- Chat command inbox and signed reply outbox. Keep across application rollback.
CREATE TABLE IF NOT EXISTS gcor.buzz_knowledge_channels (
    channel_id UUID PRIMARY KEY,
    enabled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    enabled BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS gcor.buzz_knowledge_commands (
    event_id TEXT PRIMARY KEY,
    channel_id UUID NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    response JSONB,
    draft JSONB,
    delivered_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS buzz_knowledge_pending ON gcor.buzz_knowledge_commands(next_attempt_at)
    WHERE delivered_at IS NULL;
