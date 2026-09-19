-- Close the unscoped chunk exception left by 0012. Worker access is explicit.
DROP POLICY IF EXISTS chunks_channel_scope ON gcor.chunks;
CREATE POLICY chunks_channel_scope ON gcor.chunks FOR ALL TO gcor_app
USING (EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id=chunks.document_id))
WITH CHECK (EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id=chunks.document_id));

CREATE TABLE IF NOT EXISTS gcor.buzz_synthesis_jobs (
    event_id TEXT PRIMARY KEY REFERENCES gcor.buzz_knowledge_commands(event_id),
    channel_id UUID NOT NULL,
    author TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    parts JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','ready','cancelled','stale','failed','published')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS buzz_synthesis_channel_pending ON gcor.buzz_synthesis_jobs(channel_id,created_at)
WHERE status IN ('running','ready');
ALTER TABLE gcor.buzz_synthesis_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS buzz_synthesis_scope ON gcor.buzz_synthesis_jobs;
CREATE POLICY buzz_synthesis_scope ON gcor.buzz_synthesis_jobs FOR ALL TO gcor_app
USING(channel_id::text=gcor.scope_channel()) WITH CHECK(channel_id::text=gcor.scope_channel());

-- Session contents deserve the same scope as their parent session.
ALTER TABLE gcor.knowledge_sessions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_sessions_scope ON gcor.knowledge_sessions;
CREATE POLICY knowledge_sessions_scope ON gcor.knowledge_sessions FOR ALL TO gcor_app
USING(gcor.document_in_scope(channel_id,access_level) OR gcor.scope_workload()='ingestion-worker')
WITH CHECK(gcor.document_in_scope(channel_id,access_level) OR gcor.scope_workload()='ingestion-worker');
ALTER TABLE gcor.knowledge_entries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_entries_scope ON gcor.knowledge_entries;
CREATE POLICY knowledge_entries_scope ON gcor.knowledge_entries FOR ALL TO gcor_app
USING(EXISTS(SELECT 1 FROM gcor.knowledge_sessions s WHERE s.id=session_id))
WITH CHECK(EXISTS(SELECT 1 FROM gcor.knowledge_sessions s WHERE s.id=session_id));
ALTER TABLE gcor.knowledge_participants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS knowledge_participants_scope ON gcor.knowledge_participants;
CREATE POLICY knowledge_participants_scope ON gcor.knowledge_participants FOR ALL TO gcor_app
USING(EXISTS(SELECT 1 FROM gcor.knowledge_sessions s WHERE s.id=session_id))
WITH CHECK(EXISTS(SELECT 1 FROM gcor.knowledge_sessions s WHERE s.id=session_id));

GRANT SELECT,INSERT,UPDATE,DELETE ON gcor.buzz_synthesis_jobs TO gcor_app;
