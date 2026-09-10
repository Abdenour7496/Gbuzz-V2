-- Read-time evidence validity. Approved knowledge stays visible only while every
-- supporting Buzz message still exists in its channel and every approved source it
-- depends on is itself current, recursively. The buzz-chat worker still withdraws
-- approval asynchronously for cleanup; readers no longer depend on that worker.
CREATE OR REPLACE FUNCTION gcor.knowledge_evidence_current(doc_id uuid, depth integer DEFAULT 0)
RETURNS boolean
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    doc record;
    ref text;
    dep jsonb;
    events_present boolean;
    present boolean;
BEGIN
    IF depth > 8 THEN
        RETURN false; -- dependency chains deeper than this are treated as unverifiable
    END IF;

    SELECT id, metadata, updated_at, access_level INTO doc FROM gcor.documents WHERE id = doc_id;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF depth > 0 AND (
        doc.metadata->>'knowledge_state' IS DISTINCT FROM 'approved'
        OR (doc.metadata ? 'knowledge_readers' AND doc.metadata->'knowledge_readers' <> 'null'::jsonb)
    ) THEN
        RETURN false;
    END IF;
    IF NOT (doc.metadata ? 'buzz_evidence_ids') AND NOT (doc.metadata ? 'wiki_dependencies') THEN
        RETURN true; -- ingested files and API-promoted knowledge carry no Buzz evidence
    END IF;

    -- Buzz relay events live in public.events when GCOR shares the relay database.
    events_present := to_regclass('public.events') IS NOT NULL;
    IF events_present THEN
        FOR ref IN SELECT jsonb_array_elements_text(COALESCE(doc.metadata->'buzz_evidence_ids', '[]'::jsonb)) LOOP
            IF ref !~ '^[0-9a-f]{64}$' THEN
                RETURN false;
            END IF;
            EXECUTE 'SELECT EXISTS (SELECT 1 FROM public.events e WHERE e.id = decode($1, ''hex'')'
                    || ' AND e.channel_id::text = $2 AND e.deleted_at IS NULL)'
                INTO present USING ref, doc.metadata->>'channel_id';
            IF NOT present THEN
                RETURN false;
            END IF;
        END LOOP;
    END IF;

    FOR dep IN SELECT jsonb_array_elements(COALESCE(doc.metadata->'wiki_dependencies', '[]'::jsonb)) LOOP
        IF (dep->>'id') !~ '^[0-9a-f-]{36}$' THEN
            RETURN false;
        END IF;
        SELECT EXISTS (
            SELECT 1 FROM gcor.documents source
            WHERE source.id = (dep->>'id')::uuid
              AND source.metadata->>'channel_id' = doc.metadata->>'channel_id'
              AND source.access_level = doc.access_level
              AND source.metadata->>'knowledge_state' = 'approved'
              AND source.updated_at <= doc.updated_at
              AND (NOT (source.metadata ? 'knowledge_readers') OR source.metadata->'knowledge_readers' = 'null'::jsonb)
        ) INTO present;
        IF NOT present THEN
            RETURN false;
        END IF;
        IF NOT gcor.knowledge_evidence_current((dep->>'id')::uuid, depth + 1) THEN
            RETURN false;
        END IF;
    END LOOP;

    RETURN true;
END;
$$;

COMMENT ON FUNCTION gcor.knowledge_evidence_current(uuid, integer) IS
    'True when an approved document''s supporting Buzz messages and approved dependencies are all still current. Used by every approved-only read path.';
