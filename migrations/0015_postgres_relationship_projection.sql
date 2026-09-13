-- Governed PostgreSQL-native relationship projection. Documents and chunks stay
-- authoritative; every semantic node/edge is derived, revision-bound, and
-- rebuildable. The worker is optional and dormant until its compose overlay is used.
CREATE TABLE IF NOT EXISTS gcor.relationship_projection (
    document_id UUID PRIMARY KEY REFERENCES gcor.documents(id) ON DELETE CASCADE,
    source_revision CHAR(64) NOT NULL,
    channel_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'projected', 'failed', 'stale')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    model TEXT NOT NULL,
    run_id UUID,
    node_count INTEGER NOT NULL DEFAULT 0 CHECK (node_count >= 0),
    edge_count INTEGER NOT NULL DEFAULT 0 CHECK (edge_count >= 0),
    error TEXT,
    projected_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE gcor.edges ADD COLUMN IF NOT EXISTS channel_id TEXT;
ALTER TABLE gcor.edges ADD COLUMN IF NOT EXISTS source_document_id UUID REFERENCES gcor.documents(id) ON DELETE CASCADE;
ALTER TABLE gcor.edges ADD COLUMN IF NOT EXISTS source_revision CHAR(64);
ALTER TABLE gcor.edges ADD COLUMN IF NOT EXISTS run_id UUID;
ALTER TABLE gcor.edges ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS relationship_projection_status_idx
    ON gcor.relationship_projection (status, updated_at);
-- Existing global concepts (document_id NULL) keep their prior uniqueness while
-- revision-bound derived concepts may use the same label in different sources.
DROP INDEX IF EXISTS gcor.nodes_concept_identity_idx;
CREATE UNIQUE INDEX nodes_concept_identity_idx
    ON gcor.nodes (lower(label), access_level, COALESCE(agent_id, ''), COALESCE(document_id, '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE node_type = 'Concept';
CREATE INDEX IF NOT EXISTS nodes_relationship_projection_idx
    ON gcor.nodes (document_id, ((properties->>'projector')), ((properties->>'source_revision')))
    WHERE properties->>'projector' = 'postgres_relationship_v1';
CREATE INDEX IF NOT EXISTS edges_relationship_projection_idx
    ON gcor.edges (source_document_id, source_revision, valid_to)
    WHERE properties->>'projector' = 'postgres_relationship_v1';

ALTER TABLE gcor.relationship_projection ENABLE ROW LEVEL SECURITY;
ALTER TABLE gcor.relationship_projection FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS relationship_projection_channel_scope ON gcor.relationship_projection;
CREATE POLICY relationship_projection_channel_scope ON gcor.relationship_projection
    FOR ALL TO gcor_app
    USING (gcor.scope_channel() IS NULL OR channel_id = gcor.scope_channel())
    WITH CHECK (gcor.scope_channel() IS NULL OR channel_id = gcor.scope_channel());

ALTER TABLE gcor.edges ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS edges_channel_scope ON gcor.edges;
CREATE POLICY edges_channel_scope ON gcor.edges
    FOR ALL TO gcor_app
    USING (
        gcor.scope_channel() IS NULL
        OR (channel_id = gcor.scope_channel()
            AND EXISTS (SELECT 1 FROM gcor.nodes source WHERE source.id = edges.source_id)
            AND EXISTS (SELECT 1 FROM gcor.nodes target WHERE target.id = edges.target_id))
    )
    WITH CHECK (
        gcor.scope_channel() IS NULL
        OR (channel_id = gcor.scope_channel()
            AND EXISTS (SELECT 1 FROM gcor.nodes source WHERE source.id = edges.source_id)
            AND EXISTS (SELECT 1 FROM gcor.nodes target WHERE target.id = edges.target_id))
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON gcor.relationship_projection TO gcor_app;
