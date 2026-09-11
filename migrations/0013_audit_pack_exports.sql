CREATE TABLE IF NOT EXISTS gcor.audit_pack_exports (
    id UUID PRIMARY KEY,
    channel_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    purpose TEXT NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    archive_record_count INTEGER NOT NULL CHECK (archive_record_count >= 0),
    attachment_count INTEGER NOT NULL CHECK (attachment_count >= 0),
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    pack_sha256 CHAR(64) NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL,
    signing_pubkey CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_at > start_at),
    UNIQUE (bucket, object_key)
);

CREATE INDEX IF NOT EXISTS audit_pack_exports_channel_time_idx
    ON gcor.audit_pack_exports (channel_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_pack_exports_requester_time_idx
    ON gcor.audit_pack_exports (requested_by, created_at DESC);
