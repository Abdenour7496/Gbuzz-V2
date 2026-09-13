-- Governed PostgreSQL-native relationship projection. Documents and chunks stay
-- authoritative; every semantic node/edge is derived, revision-bound, and
-- rebuildable. The worker is optional and dormant until its compose overlay is used.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gcor_relationship_projector') THEN
        CREATE ROLE gcor_relationship_projector NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END
$$;

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
    lease_owner UUID,
    lease_expires_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
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
    FOR ALL TO gcor_app, gcor_relationship_projector
    USING (channel_id = gcor.scope_channel() OR gcor.scope_workload() = 'relationship-projector')
    WITH CHECK (channel_id = gcor.scope_channel() OR gcor.scope_workload() = 'relationship-projector');

-- Add narrowly scoped worker policies. Never replace the interactive policies
-- established by 0014_tenant_rls_fail_closed.sql.
DROP POLICY IF EXISTS relationship_documents_select ON gcor.documents;
CREATE POLICY relationship_documents_select ON gcor.documents FOR SELECT TO gcor_relationship_projector
    USING (gcor.scope_workload() = 'relationship-projector');
DROP POLICY IF EXISTS relationship_chunks_select ON gcor.chunks;
CREATE POLICY relationship_chunks_select ON gcor.chunks FOR SELECT TO gcor_relationship_projector
    USING (gcor.scope_workload() = 'relationship-projector');
DROP POLICY IF EXISTS relationship_nodes_worker ON gcor.nodes;
DROP POLICY IF EXISTS relationship_nodes_select ON gcor.nodes;
DROP POLICY IF EXISTS relationship_nodes_insert ON gcor.nodes;
DROP POLICY IF EXISTS relationship_nodes_update ON gcor.nodes;
CREATE POLICY relationship_nodes_select ON gcor.nodes FOR SELECT TO gcor_relationship_projector
    USING (gcor.scope_workload() = 'relationship-projector'
           AND properties->>'projector' = 'postgres_relationship_v1' AND document_id IS NOT NULL);
CREATE POLICY relationship_nodes_insert ON gcor.nodes FOR INSERT TO gcor_relationship_projector
    WITH CHECK (gcor.scope_workload() = 'relationship-projector'
                AND properties->>'projector' = 'postgres_relationship_v1' AND document_id IS NOT NULL
                AND properties->>'source_revision' IS NOT NULL AND properties->>'channel_id' IS NOT NULL);
CREATE POLICY relationship_nodes_update ON gcor.nodes FOR UPDATE TO gcor_relationship_projector
    USING (gcor.scope_workload() = 'relationship-projector'
           AND properties->>'projector' = 'postgres_relationship_v1' AND document_id IS NOT NULL
           AND properties->>'source_revision' IS NOT NULL AND properties->>'channel_id' IS NOT NULL)
    WITH CHECK (gcor.scope_workload() = 'relationship-projector'
                AND properties->>'projector' = 'postgres_relationship_v1' AND document_id IS NOT NULL
                AND properties->>'source_revision' IS NOT NULL AND properties->>'channel_id' IS NOT NULL);
DROP POLICY IF EXISTS relationship_edges_worker ON gcor.edges;
DROP POLICY IF EXISTS relationship_edges_select ON gcor.edges;
DROP POLICY IF EXISTS relationship_edges_insert ON gcor.edges;
DROP POLICY IF EXISTS relationship_edges_update ON gcor.edges;
CREATE POLICY relationship_edges_select ON gcor.edges FOR SELECT TO gcor_relationship_projector
    USING (gcor.scope_workload() = 'relationship-projector'
           AND properties->>'projector' = 'postgres_relationship_v1' AND channel_id IS NOT NULL
           AND source_document_id IS NOT NULL AND source_revision IS NOT NULL);
CREATE POLICY relationship_edges_insert ON gcor.edges FOR INSERT TO gcor_relationship_projector
    WITH CHECK (gcor.scope_workload() = 'relationship-projector'
                AND properties->>'projector' = 'postgres_relationship_v1' AND channel_id IS NOT NULL
                AND source_document_id IS NOT NULL AND source_revision IS NOT NULL);
CREATE POLICY relationship_edges_update ON gcor.edges FOR UPDATE TO gcor_relationship_projector
    USING (gcor.scope_workload() = 'relationship-projector'
           AND properties->>'projector' = 'postgres_relationship_v1' AND channel_id IS NOT NULL
           AND source_document_id IS NOT NULL AND source_revision IS NOT NULL)
    WITH CHECK (gcor.scope_workload() = 'relationship-projector'
                AND properties->>'projector' = 'postgres_relationship_v1' AND channel_id IS NOT NULL
                AND source_document_id IS NOT NULL AND source_revision IS NOT NULL);

GRANT SELECT, INSERT, UPDATE, DELETE ON gcor.relationship_projection TO gcor_app;
GRANT USAGE ON SCHEMA gcor TO gcor_relationship_projector;
GRANT SELECT ON gcor.documents, gcor.chunks TO gcor_relationship_projector;
REVOKE DELETE ON gcor.nodes, gcor.edges FROM gcor_relationship_projector;
GRANT SELECT, INSERT, UPDATE ON gcor.nodes, gcor.edges TO gcor_relationship_projector;
GRANT SELECT, INSERT, UPDATE, DELETE ON gcor.relationship_projection TO gcor_relationship_projector;
