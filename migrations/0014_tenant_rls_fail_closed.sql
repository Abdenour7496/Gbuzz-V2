-- Fail-closed tenant isolation for runtime services. Maintenance exceptions are
-- explicit workload capabilities, never the absence of scope.
CREATE OR REPLACE FUNCTION gcor.scope_workload() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('gcor.workload', true), '') $$;

CREATE OR REPLACE FUNCTION gcor.document_in_scope(doc_channel text, doc_access text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT gcor.scope_channel() IS NOT NULL AND doc_channel IS NOT DISTINCT FROM gcor.scope_channel()
       AND (gcor.scope_access_level() IS NULL OR doc_access = gcor.scope_access_level())
$$;

CREATE POLICY documents_ingestion_worker ON gcor.documents FOR ALL TO gcor_app
USING (gcor.scope_workload()='ingestion-worker') WITH CHECK (gcor.scope_workload()='ingestion-worker');

DROP POLICY IF EXISTS nodes_channel_scope ON gcor.nodes;
CREATE POLICY nodes_channel_scope ON gcor.nodes FOR ALL TO gcor_app
USING (gcor.scope_workload()='ingestion-worker' OR (document_id IS NOT NULL AND EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id=nodes.document_id))
    OR (document_id IS NULL AND properties->>'channel_id'=gcor.scope_channel()))
WITH CHECK (gcor.scope_workload()='ingestion-worker' OR (document_id IS NOT NULL AND EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id=nodes.document_id))
    OR (document_id IS NULL AND properties->>'channel_id'=gcor.scope_channel()));

ALTER TABLE gcor.edges ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS edges_channel_scope ON gcor.edges;
CREATE POLICY edges_channel_scope ON gcor.edges FOR ALL TO gcor_app
USING (EXISTS (SELECT 1 FROM gcor.nodes s,gcor.nodes t WHERE s.id=edges.source_id AND t.id=edges.target_id))
WITH CHECK (EXISTS (SELECT 1 FROM gcor.nodes s,gcor.nodes t WHERE s.id=edges.source_id AND t.id=edges.target_id));

ALTER TABLE gcor.ingestion_records ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingestion_records_scope ON gcor.ingestion_records FOR ALL TO gcor_app
USING (channel_id=gcor.scope_channel() OR gcor.scope_workload()='ingestion-worker')
WITH CHECK (channel_id=gcor.scope_channel() OR gcor.scope_workload()='ingestion-worker');

ALTER TABLE gcor.ingestion_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingestion_jobs_scope ON gcor.ingestion_jobs FOR ALL TO gcor_app
USING (channel_id=gcor.scope_channel() OR gcor.scope_workload()='ingestion-worker')
WITH CHECK (channel_id=gcor.scope_channel() OR gcor.scope_workload()='ingestion-worker');

ALTER TABLE gcor.knowledge_feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY knowledge_feedback_scope ON gcor.knowledge_feedback FOR ALL TO gcor_app
USING (channel_id=gcor.scope_channel()) WITH CHECK (channel_id=gcor.scope_channel());

ALTER TABLE gcor.governance_outbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY governance_outbox_scope ON gcor.governance_outbox FOR ALL TO gcor_app
USING (gcor.scope_workload()='governance-publisher' OR EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id=governance_outbox.document_id))
WITH CHECK (gcor.scope_workload()='governance-publisher' OR EXISTS (SELECT 1 FROM gcor.documents d WHERE d.id=governance_outbox.document_id));

ALTER TABLE gcor.audit_pack_exports ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_pack_exports_scope ON gcor.audit_pack_exports FOR ALL TO gcor_app
USING (channel_id=gcor.scope_channel()) WITH CHECK (channel_id=gcor.scope_channel());
