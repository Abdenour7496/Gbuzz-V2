import hashlib
import json

from fastapi import HTTPException
from governance_outbox import build_event, json_object


def request_hash(operation, payload):
    return hashlib.sha256(json.dumps({"operation": operation, "payload": payload.model_dump()},
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def commit_governance(request, document_id, patch, action, actor, note,
                            idempotency_key, fingerprint, response_action, default_bucket,
                            queue_graphiti, superseded_by=None, expected_updated_at=None, principal=None):
    if not request.app.state.governance_available:
        raise HTTPException(503, "Governance requires migration 0007_governance_outbox.sql; no changes were applied")
    if idempotency_key is not None and not (1 <= len(idempotency_key) <= 200 and idempotency_key.strip()):
        raise HTTPException(422, "Idempotency-Key must contain 1 to 200 characters")
    async with request.app.state.pool.acquire() as connection:
        async with connection.transaction():
            # Serialize channel review transactions before taking document locks. Revision approval
            # locks both proposal and base, so concurrent competing proposals cannot deadlock or win twice.
            if principal is not None:
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",'knowledge-review:'+principal.channel_id)
            if idempotency_key is not None:
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", idempotency_key)
                prior = await connection.fetchrow(
                    "SELECT request_hash,response FROM gcor.governance_outbox WHERE idempotency_key=$1", idempotency_key)
                if prior is not None:
                    if prior["request_hash"] != fingerprint:
                        raise HTTPException(409, "Idempotency-Key was already used for a different governance request")
                    return json_object(prior["response"])
            if principal is not None:
                target = await connection.fetchrow("SELECT metadata,access_level,updated_at FROM gcor.documents WHERE id=$1 FOR UPDATE",document_id)
                if target is None or json_object(target['metadata']).get('channel_id')!=principal.channel_id or target['access_level']!=principal.access_level:
                    raise HTTPException(404,'Knowledge document not found')
                readers=json_object(target['metadata']).get('knowledge_readers')
                if readers is not None and (not isinstance(readers,list) or principal.subject not in readers):
                    raise HTTPException(404,'Knowledge document not found')
                if target['updated_at']!=expected_updated_at:
                    raise HTTPException(409,'Knowledge changed since it was opened; reload before reviewing')
                if superseded_by is not None and not await connection.fetchval(
                    "SELECT id FROM gcor.documents WHERE id=$1 AND metadata->>'channel_id'=$2 AND access_level=$3",superseded_by,principal.channel_id,principal.access_level):
                    raise HTTPException(404,'Superseding document not found in this channel')
            if patch.get('knowledge_state')=='approved':
                review_meta=await connection.fetchval('SELECT metadata FROM gcor.documents WHERE id=$1 FOR UPDATE',document_id)
                from buzz_wiki import approve_dependencies
                await approve_dependencies(connection,request,document_id,json_object(review_meta) if review_meta else {},principal,actor,default_bucket,queue_graphiti)
            row = await connection.fetchrow(
                """UPDATE gcor.documents SET updated_at=now(),
                   metadata=COALESCE(metadata, '{}'::jsonb) || $2::jsonb
                   WHERE id=$1 RETURNING id,title,source_uri,metadata""", document_id, json.dumps(patch))
            if row is None:
                raise HTTPException(404, "Knowledge document not found")
            if superseded_by is not None:
                if superseded_by == document_id:
                    raise HTTPException(422, "A document cannot supersede itself")
                if not await connection.fetchval("SELECT id FROM gcor.documents WHERE id=$1 FOR KEY SHARE", superseded_by):
                    raise HTTPException(404, "Superseding document not found")
                await connection.execute(
                    """INSERT INTO gcor.edges(source_id,target_id,relation)
                       SELECT source.id,target.id,'RELATES_TO'
                       FROM (SELECT id FROM gcor.nodes WHERE document_id=$1 AND node_type='Document'
                             ORDER BY created_at,id LIMIT 1) source
                       CROSS JOIN (SELECT id FROM gcor.nodes WHERE document_id=$2 AND node_type='Document'
                                   ORDER BY created_at,id LIMIT 1) target
                       ON CONFLICT DO NOTHING""", document_id, superseded_by)
            metadata = json_object(row["metadata"])
            await queue_graphiti(request, document_id, action, connection=connection)
            event_id, bucket, key, event = build_event(document_id, action, metadata, actor, note, default_bucket)
            response = {"action": response_action, "document_id": str(document_id), "title": row["title"],
                        "source_uri": row["source_uri"], "knowledge_state": metadata.get("knowledge_state"),
                        "governance_bucket": bucket, "governance_event_key": key,
                        "governance_event_id": str(event_id), "governance_publication_status": "pending"}
            if response_action == "transition":
                response["knowledge_transition"] = metadata.get("knowledge_transition")
            await connection.execute(
                """INSERT INTO gcor.governance_outbox
                   (event_id,document_id,bucket,object_key,payload,response,idempotency_key,request_hash)
                   VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8)""",
                event_id, document_id, bucket, key, json.dumps(event), json.dumps(response), idempotency_key, fingerprint)
    return response
