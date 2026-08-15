-- PostgreSQL is the authoritative session-centric knowledge layer.
-- Graphiti/FalkorDB is a rebuildable projection and owns temporal graph semantics.
-- Legacy native-graph data is deliberately preserved for rollback/audit safety. The
-- application no longer writes to it; all new graph and temporal work is projected
-- through Graphiti MCP.

CREATE TABLE IF NOT EXISTS gcor.knowledge_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_session_id TEXT NOT NULL,
    channel_id TEXT,
    channel_name TEXT,
    access_level TEXT NOT NULL DEFAULT 'public',
    agent_id TEXT,
    title TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_sessions_identity_idx
    ON gcor.knowledge_sessions (
        external_session_id,
        COALESCE(channel_id, ''),
        access_level,
        COALESCE(agent_id, '')
    );
CREATE INDEX IF NOT EXISTS knowledge_sessions_channel_idx
    ON gcor.knowledge_sessions (channel_id, started_at DESC);

CREATE TABLE IF NOT EXISTS gcor.knowledge_participants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES gcor.knowledge_sessions(id) ON DELETE CASCADE,
    participant_type TEXT NOT NULL CHECK (participant_type IN ('user', 'agent', 'document', 'system')),
    external_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    document_id UUID REFERENCES gcor.documents(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, participant_type, external_id)
);

CREATE INDEX IF NOT EXISTS knowledge_participants_document_idx
    ON gcor.knowledge_participants (document_id)
    WHERE document_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS gcor.knowledge_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES gcor.knowledge_sessions(id) ON DELETE CASCADE,
    participant_id UUID NOT NULL REFERENCES gcor.knowledge_participants(id) ON DELETE RESTRICT,
    source_document_id UUID REFERENCES gcor.documents(id) ON DELETE SET NULL,
    source_record_id UUID NOT NULL REFERENCES gcor.ingestion_records(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('message', 'document', 'system')),
    content TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    sequence_no BIGINT,
    schema_version TEXT NOT NULL DEFAULT '1.0.0',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_record_id, external_id, entry_type)
);

CREATE INDEX IF NOT EXISTS knowledge_entries_session_time_idx
    ON gcor.knowledge_entries (session_id, occurred_at, sequence_no);
CREATE INDEX IF NOT EXISTS knowledge_entries_document_idx
    ON gcor.knowledge_entries (source_document_id);

CREATE TABLE IF NOT EXISTS gcor.graphiti_projection (
    entry_id UUID PRIMARY KEY REFERENCES gcor.knowledge_entries(id) ON DELETE CASCADE,
    graphiti_episode_id UUID NOT NULL UNIQUE,
    group_id TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT 'add' CHECK (operation IN ('add', 'delete')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'submitted', 'failed', 'skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    previous_episode_id UUID REFERENCES gcor.knowledge_entries(id) ON DELETE SET NULL,
    error TEXT,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE gcor.graphiti_projection
    ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS graphiti_projection_work_idx
    ON gcor.graphiti_projection (status, next_attempt_at, created_at)
    WHERE status IN ('pending', 'failed');

CREATE OR REPLACE VIEW gcor.session_knowledge AS
SELECT
    e.id AS entry_id,
    s.id AS session_id,
    s.external_session_id,
    s.channel_id,
    s.channel_name,
    s.access_level,
    s.agent_id,
    p.id AS participant_id,
    p.participant_type,
    p.external_id AS participant_external_id,
    p.display_name AS participant_name,
    e.entry_type,
    e.external_id AS entry_external_id,
    e.content,
    e.occurred_at,
    e.sequence_no,
    e.source_document_id,
    e.source_record_id,
    e.metadata,
    gp.graphiti_episode_id,
    gp.group_id,
    gp.operation AS graphiti_operation,
    gp.status AS graphiti_status,
    gp.attempts AS graphiti_attempts,
    gp.error AS graphiti_error,
    gp.reconciled_at AS graphiti_reconciled_at
FROM gcor.knowledge_entries e
JOIN gcor.knowledge_sessions s ON s.id=e.session_id
JOIN gcor.knowledge_participants p ON p.id=e.participant_id
LEFT JOIN gcor.graphiti_projection gp ON gp.entry_id=e.id;

-- Additive backfill for already-governed records. New ingestions use the application path,
-- which can preserve individual messages from structured session JSON.
INSERT INTO gcor.knowledge_sessions
    (external_session_id,channel_id,channel_name,access_level,agent_id,title,started_at,metadata)
SELECT DISTINCT ON (
        COALESCE(r.metadata->>'session_id',r.channel_id,'document:' || d.id::text),
        COALESCE(r.channel_id,''),d.access_level,COALESCE(d.agent_id,'')
    )
    COALESCE(r.metadata->>'session_id',r.channel_id,'document:' || d.id::text),
    r.channel_id,r.channel_name,d.access_level,d.agent_id,
    COALESCE(r.channel_name,'Session ' || COALESCE(r.metadata->>'session_id',r.channel_id,d.id::text)),
    COALESCE(NULLIF(r.metadata->>'event_timestamp','')::timestamptz,r.created_at),
    jsonb_build_object('schema_version','1.0.0','backfilled',true)
FROM gcor.ingestion_records r
JOIN gcor.documents d ON d.id=r.document_id
WHERE r.status IN ('indexed','backfilled')
ORDER BY
    COALESCE(r.metadata->>'session_id',r.channel_id,'document:' || d.id::text),
    COALESCE(r.channel_id,''),d.access_level,COALESCE(d.agent_id,''),r.created_at
ON CONFLICT DO NOTHING;

WITH records AS (
    SELECT r.*,d.access_level,d.agent_id,d.title,d.media_type,
           s.id AS knowledge_session_id,
           CASE
             WHEN COALESCE(r.metadata->>'file_name','')<>''
               OR COALESCE(r.metadata->>'file_url','')<>''
               OR r.metadata->>'record_type' IN ('attachment','buzz_attachment','document','file_upload')
               THEN 'document'
             WHEN COALESCE(r.metadata->>'author_pubkey','')<>'' THEN 'user'
             WHEN d.agent_id IS NOT NULL THEN 'agent'
             ELSE 'system'
           END AS participant_type
    FROM gcor.ingestion_records r
    JOIN gcor.documents d ON d.id=r.document_id
    JOIN gcor.knowledge_sessions s
      ON s.external_session_id=COALESCE(r.metadata->>'session_id',r.channel_id,'document:' || d.id::text)
     AND COALESCE(s.channel_id,'')=COALESCE(r.channel_id,'')
     AND s.access_level=d.access_level
     AND COALESCE(s.agent_id,'')=COALESCE(d.agent_id,'')
    WHERE r.status IN ('indexed','backfilled')
)
INSERT INTO gcor.knowledge_participants
    (session_id,participant_type,external_id,display_name,document_id,metadata)
SELECT knowledge_session_id,participant_type,
       CASE participant_type
         WHEN 'document' THEN document_id::text
         WHEN 'user' THEN metadata->>'author_pubkey'
         WHEN 'agent' THEN agent_id
         ELSE 'gbuzz'
       END,
       CASE participant_type
         WHEN 'document' THEN title
         WHEN 'user' THEN COALESCE(metadata->>'author_name',metadata->>'author_pubkey')
         WHEN 'agent' THEN COALESCE(metadata->>'agent_name',agent_id)
         ELSE 'Gbuzz'
       END,
       CASE WHEN participant_type='document' THEN document_id ELSE NULL END,
       jsonb_build_object('schema_version','1.0.0','backfilled',true)
FROM records
ON CONFLICT (session_id,participant_type,external_id) DO NOTHING;

WITH records AS (
    SELECT r.*,d.access_level,d.agent_id,d.title,d.media_type,
           s.id AS knowledge_session_id,
           CASE
             WHEN COALESCE(r.metadata->>'file_name','')<>''
               OR COALESCE(r.metadata->>'file_url','')<>''
               OR r.metadata->>'record_type' IN ('attachment','buzz_attachment','document','file_upload')
               THEN 'document'
             WHEN COALESCE(r.metadata->>'author_pubkey','')<>'' THEN 'user'
             WHEN d.agent_id IS NOT NULL THEN 'agent'
             ELSE 'system'
           END AS participant_type,
           COALESCE((SELECT string_agg(c.content,' ' ORDER BY c.ordinal)
                     FROM gcor.chunks c WHERE c.document_id=d.id),d.title) AS entry_content
    FROM gcor.ingestion_records r
    JOIN gcor.documents d ON d.id=r.document_id
    JOIN gcor.knowledge_sessions s
      ON s.external_session_id=COALESCE(r.metadata->>'session_id',r.channel_id,'document:' || d.id::text)
     AND COALESCE(s.channel_id,'')=COALESCE(r.channel_id,'')
     AND s.access_level=d.access_level
     AND COALESCE(s.agent_id,'')=COALESCE(d.agent_id,'')
    WHERE r.status IN ('indexed','backfilled')
), resolved AS (
    SELECT records.*,p.id AS participant_id
    FROM records
    JOIN gcor.knowledge_participants p
      ON p.session_id=records.knowledge_session_id
     AND p.participant_type=records.participant_type
     AND p.external_id=CASE records.participant_type
         WHEN 'document' THEN records.document_id::text
         WHEN 'user' THEN records.metadata->>'author_pubkey'
         WHEN 'agent' THEN records.agent_id
         ELSE 'gbuzz'
       END
)
INSERT INTO gcor.knowledge_entries
    (session_id,participant_id,source_document_id,source_record_id,external_id,
     entry_type,content,occurred_at,metadata)
SELECT knowledge_session_id,participant_id,document_id,id,
       COALESCE(event_id,id::text),
       CASE participant_type WHEN 'document' THEN 'document'
            WHEN 'system' THEN 'system' ELSE 'message' END,
       entry_content,
       COALESCE(NULLIF(metadata->>'event_timestamp','')::timestamptz,created_at),
       jsonb_build_object(
           'schema_version','1.0.0','backfilled',true,'source_uri',source_uri,
           'source_document_id',document_id,'source_record_id',id,
           'bucket',bucket,'original_key',original_key,'markdown_key',markdown_key,'record_key',record_key
       )
FROM resolved
ON CONFLICT (source_record_id,external_id,entry_type) DO NOTHING;

INSERT INTO gcor.graphiti_projection
    (entry_id,graphiti_episode_id,group_id,previous_episode_id,status)
SELECT e.id,e.id,e.session_id::text,
       lag(e.id) OVER (PARTITION BY e.session_id ORDER BY e.occurred_at,e.created_at,e.id),
       CASE WHEN d.metadata->>'knowledge_state' IN ('proposed','rejected','archived')
            THEN 'skipped' ELSE 'pending' END
FROM gcor.knowledge_entries e
LEFT JOIN gcor.documents d ON d.id=e.source_document_id
ON CONFLICT (entry_id) DO NOTHING;
