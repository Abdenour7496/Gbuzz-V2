-- Graphiti extraction is deliberately fed bounded document contributions. PostgreSQL
-- remains authoritative; these child entries are deterministic and can rebuild the graph.
INSERT INTO gcor.knowledge_entries
    (session_id,participant_id,source_document_id,source_record_id,external_id,
     entry_type,content,occurred_at,sequence_no,schema_version,metadata)
SELECT parent.session_id,parent.participant_id,parent.source_document_id,parent.source_record_id,
       parent.external_id || ':chunk:' || chunk.ordinal::text,
       'document',chunk.content,parent.occurred_at,chunk.ordinal,parent.schema_version,
       parent.metadata || jsonb_build_object(
           'parent_entry_id',parent.id,
           'parent_external_id',parent.external_id,
           'segment_ordinal',chunk.ordinal,
           'segment_count',(SELECT count(*) FROM gcor.chunks siblings
                            WHERE siblings.document_id=parent.source_document_id),
           'bounded_graphiti_episode',true
       )
FROM gcor.knowledge_entries parent
JOIN gcor.chunks chunk ON chunk.document_id=parent.source_document_id
WHERE parent.entry_type='document'
  AND NOT (parent.metadata ? 'segment_ordinal')
ON CONFLICT (source_record_id,external_id,entry_type) DO NOTHING;

INSERT INTO gcor.graphiti_projection
    (entry_id,graphiti_episode_id,group_id,previous_episode_id,status)
SELECT e.id,e.id,e.session_id::text,
       lag(e.id) OVER (PARTITION BY e.session_id
                       ORDER BY e.occurred_at,e.sequence_no NULLS FIRST,e.created_at,e.id),
       CASE WHEN d.metadata->>'knowledge_state' IN ('proposed','rejected','archived')
            THEN 'skipped' ELSE 'pending' END
FROM gcor.knowledge_entries e
LEFT JOIN gcor.documents d ON d.id=e.source_document_id
ON CONFLICT (entry_id) DO NOTHING;

UPDATE gcor.knowledge_entries parent
SET metadata=parent.metadata || jsonb_build_object('segmented_parent',true)
WHERE parent.entry_type='document'
  AND NOT (parent.metadata ? 'segment_ordinal')
  AND parent.metadata->>'segmented_parent' IS DISTINCT FROM 'true'
  AND EXISTS (
      SELECT 1 FROM gcor.knowledge_entries child
      WHERE child.source_record_id=parent.source_record_id
        AND child.metadata->>'parent_entry_id'=parent.id::text
  );

UPDATE gcor.graphiti_projection projection
SET status='skipped',error='Superseded by bounded document contribution episodes',updated_at=now()
FROM gcor.knowledge_entries parent
WHERE projection.entry_id=parent.id
  AND parent.metadata->>'segmented_parent'='true'
  AND projection.operation='add'
  AND (
      projection.status<>'skipped'
      OR projection.error IS DISTINCT FROM 'Superseded by bounded document contribution episodes'
  );
