SET client_min_messages = warning;
-- Row-level security as defense in depth for the runtime role. Application code
-- still filters by channel and access level; these policies make a query that
-- forgets to do so return nothing outside the caller's scope instead of leaking.
--
-- Scope arrives as session settings that proxy/db_scope.py applies to every pooled
-- connection while a signed Buzz identity is active:
--   gcor.channel_id    channel the identity is bound to
--   gcor.access_level  access level the identity is bound to
-- Without a scope (legacy shared-secret API, background workers, migrations) the
-- policies permit every row. Table owners are never subject to RLS, so deployments
-- that still run as POSTGRES_USER are unaffected until they adopt GCOR_DB_USER.
CREATE OR REPLACE FUNCTION gcor.scope_channel() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('gcor.channel_id', true), '') $$;

CREATE OR REPLACE FUNCTION gcor.scope_access_level() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('gcor.access_level', true), '') $$;

CREATE OR REPLACE FUNCTION gcor.document_in_scope(doc_channel text, doc_access text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT gcor.scope_channel() IS NULL
        OR (doc_channel IS NOT DISTINCT FROM gcor.scope_channel()
            AND (gcor.scope_access_level() IS NULL OR doc_access = gcor.scope_access_level()))
$$;

ALTER TABLE gcor.documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS documents_channel_scope ON gcor.documents;
CREATE POLICY documents_channel_scope ON gcor.documents
    FOR ALL TO gcor_app
    USING (gcor.document_in_scope(metadata->>'channel_id', access_level))
    WITH CHECK (gcor.document_in_scope(metadata->>'channel_id', access_level));

ALTER TABLE gcor.chunks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chunks_channel_scope ON gcor.chunks;
CREATE POLICY chunks_channel_scope ON gcor.chunks
    FOR ALL TO gcor_app
    USING (gcor.scope_channel() IS NULL OR EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id = chunks.document_id))
    WITH CHECK (gcor.scope_channel() IS NULL OR EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id = chunks.document_id));

ALTER TABLE gcor.nodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS nodes_channel_scope ON gcor.nodes;
CREATE POLICY nodes_channel_scope ON gcor.nodes
    FOR ALL TO gcor_app
    USING (gcor.scope_channel() IS NULL OR nodes.document_id IS NULL
           OR EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id = nodes.document_id))
    WITH CHECK (gcor.scope_channel() IS NULL OR nodes.document_id IS NULL
           OR EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id = nodes.document_id));

-- The documents subqueries above run under the same policies, so a chunk or node is
-- visible only when its document is. Edges carry identifiers only and are reached
-- through nodes; ingestion records, jobs, outbox and projection tables hold no
-- channel content and stay unrestricted for the workers that drive them.
