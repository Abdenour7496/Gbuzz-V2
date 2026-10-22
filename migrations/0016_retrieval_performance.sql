-- Retrieval performance at volume. Additive: one generated column and indexes; no data change.
--
-- Under row-level security PostgreSQL cannot use an index for a non-leakproof user filter such
-- as metadata->>'channel_id' = $1 (jsonb ->> is not leakproof), so every authenticated retrieval
-- scanned all documents. A stored generated column compared with plain text equality (leakproof)
-- is index-eligible under RLS. The column is derived, so it can never disagree with metadata.
ALTER TABLE gcor.documents
    ADD COLUMN IF NOT EXISTS channel_id text GENERATED ALWAYS AS (metadata->>'channel_id') STORED;
CREATE INDEX IF NOT EXISTS documents_channel_access_idx ON gcor.documents (channel_id, access_level);
-- RLS policies on nodes/chunks/edges and graph walks look documents and nodes up by document id.
CREATE INDEX IF NOT EXISTS nodes_document_id_idx ON gcor.nodes (document_id);
ANALYZE gcor.documents;
ANALYZE gcor.nodes;
