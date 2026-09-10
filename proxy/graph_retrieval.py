"""Graph-assisted candidate discovery; only authoritative, authorized chunks leave GCOR."""
import asyncio
import json
import os
from uuid import UUID

import httpx


def supported_documents(facts,episodes,groups):
    documents=set()
    for fact in facts:
        if not isinstance(fact,dict) or fact.get('group_id') not in groups or fact.get('expired_at') or fact.get('invalid_at'):
            continue
        references=fact.get('episodes',[])
        if not isinstance(references,list) or not references or not all(isinstance(e,str) and e in episodes for e in references):continue
        documents.update(episodes[e] for e in references)
    return list(documents)[:30]


async def candidates(pool,query,channel_id,access_level,agent_id,subject=None):
    if not channel_id or os.getenv('GRAPHITI_RETRIEVAL_ENABLED','false')!='true':return []
    try:
        async with asyncio.timeout(3):
            rows=await pool.fetch('''SELECT gp.graphiti_episode_id,gp.group_id,d.id
                FROM gcor.graphiti_projection gp JOIN gcor.knowledge_entries e ON e.id=gp.entry_id
                JOIN gcor.documents d ON d.id=e.source_document_id
                WHERE gp.reconciled_at IS NOT NULL AND gp.operation='add'
                  AND d.metadata->>'channel_id'=$1 AND d.access_level=$2
                  AND d.metadata->>'knowledge_state'='approved' AND gcor.knowledge_evidence_current(d.id)
                  AND ($3::text IS NULL OR d.agent_id=$3)
                  AND ($4::text IS NULL OR NOT(d.metadata ? 'knowledge_readers') OR d.metadata->'knowledge_readers'='null'::jsonb OR d.metadata->'knowledge_readers' @> jsonb_build_array($4::text))
                  LIMIT 5000''',channel_id,access_level,agent_id,subject,timeout=2)
            episodes={str(r['graphiti_episode_id']):r['id'] for r in rows}
            groups=list({r['group_id'] for r in rows})[:20]
            if not groups:return []
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            async with httpx.AsyncClient(headers={'Host':'localhost:8000'},follow_redirects=True,trust_env=False) as client:
                async with streamable_http_client('http://graphiti-mcp:8000/mcp',http_client=client) as streams:
                    async with ClientSession(streams[0],streams[1]) as session:
                        await session.initialize()
                        result=await session.call_tool('search_memory_facts',{'query':query,'group_ids':groups,'max_facts':10})
                        if result.isError:return []
                        obj=getattr(result,'structuredContent',None)
                        if not isinstance(obj,dict):
                            obj=next((json.loads(c.text) for c in result.content if getattr(c,'text',None)),{})
                        obj=obj.get('result',obj)
                        return supported_documents(obj.get('facts',[]),episodes,set(groups))
    except Exception:
        return []
