CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS gcor;

CREATE TABLE IF NOT EXISTS gcor.documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_sha256 CHAR(64) NOT NULL UNIQUE,
    title TEXT NOT NULL,
    source_uri TEXT,
    object_key TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'text/plain',
    access_level TEXT NOT NULL DEFAULT 'public',
    agent_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gcor.nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES gcor.documents(id) ON DELETE CASCADE,
    node_type TEXT NOT NULL CHECK (node_type IN (
        'Document', 'Chunk', 'Memory', 'Inference', 'Belief', 'Goal', 'Event', 'Concept'
    )),
    label TEXT NOT NULL,
    content TEXT,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    access_level TEXT NOT NULL DEFAULT 'public',
    agent_id TEXT,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to TIMESTAMPTZ,
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS gcor.chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES gcor.documents(id) ON DELETE CASCADE,
    node_id UUID NOT NULL UNIQUE REFERENCES gcor.nodes(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    content TEXT NOT NULL,
    token_count INTEGER,
    embedding vector(__EMBEDDING_DIMS__) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);

CREATE TABLE IF NOT EXISTS gcor.edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES gcor.nodes(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES gcor.nodes(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN (
        'CONTAINS', 'DERIVED_FROM', 'HOLDS', 'ABOUT', 'CONTRADICTS', 'SUPPORTS', 'RELATES_TO'
    )),
    weight REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0),
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, target_id, relation),
    CHECK (source_id <> target_id)
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON gcor.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS documents_access_idx ON gcor.documents (access_level, agent_id);
CREATE INDEX IF NOT EXISTS nodes_filter_idx
    ON gcor.nodes (access_level, agent_id, confidence, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS edges_source_idx ON gcor.edges (source_id);
CREATE INDEX IF NOT EXISTS edges_target_idx ON gcor.edges (target_id);