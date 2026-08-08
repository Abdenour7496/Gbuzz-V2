ALTER TABLE gcor.chunks
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX IF NOT EXISTS chunks_search_vector_gin_idx
    ON gcor.chunks USING gin (search_vector);

ALTER TABLE gcor.edges DROP CONSTRAINT IF EXISTS edges_relation_check;
ALTER TABLE gcor.edges ADD CONSTRAINT edges_relation_check CHECK (relation IN (
    'CONTAINS', 'DERIVED_FROM', 'HOLDS', 'ABOUT', 'MENTIONS',
    'CONTRADICTS', 'SUPPORTS', 'RELATES_TO'
));

CREATE UNIQUE INDEX IF NOT EXISTS nodes_concept_identity_idx
    ON gcor.nodes (lower(label), access_level, COALESCE(agent_id, ''))
    WHERE node_type = 'Concept';

CREATE OR REPLACE VIEW gcor.extension_capabilities AS
SELECT
    EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS pgvector_enabled,
    EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'age') AS apache_age_enabled,
    EXISTS (SELECT 1 FROM pg_extension WHERE extname IN ('vectorscale', 'pgvectorscale')) AS pgvectorscale_enabled,
    EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'age') AS apache_age_available,
    EXISTS (SELECT 1 FROM pg_available_extensions WHERE name IN ('vectorscale', 'pgvectorscale')) AS pgvectorscale_available;