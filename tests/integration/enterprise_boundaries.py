"""Run inside the isolated integration proxy; never against live knowledge."""
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

TOKEN = 'isolated-enterprise-reader'
CHANNEL = 'boundary-' + str(uuid4())
os.environ['GCOR_ACCESS_MODE'] = 'scoped'
os.environ['GCOR_SCOPED_CREDENTIALS'] = json.dumps([{'sha256':hashlib.sha256(TOKEN.encode()).hexdigest(),
    'subject':'reader','channel_id':CHANNEL,'access_level':'public'}])
import main


async def run():
    async with main.lifespan(main.app):
        pool = main.app.state.pool
        # Independent documents isolate authorization from coincidental ranking.
        nodes=[];documents=[]
        for channel, state in [(CHANNEL,'approved'),('boundary-b','approved'),(CHANNEL,'proposed'),(CHANNEL,None),(CHANNEL,'restricted')]:
            document, node = uuid4(),uuid4()
            metadata={'channel_id':channel}
            if state: metadata['knowledge_state']=state
            if state=='restricted':metadata.update(knowledge_state='approved',knowledge_readers=['other-reader'])
            digest=hashlib.sha256(str(document).encode()).hexdigest()
            await pool.execute('''INSERT INTO gcor.documents(id,content_sha256,identity_sha256,title,object_key,metadata)
                                  VALUES($1,$2,$2,'Boundary document','test', $3::jsonb)''',document,digest,json.dumps(metadata))
            await pool.execute("INSERT INTO gcor.nodes(id,document_id,node_type,label,content) VALUES($1,$2,'Chunk','boundary','boundary evidence')",node,document)
            await pool.execute("INSERT INTO gcor.chunks(document_id,node_id,ordinal,content,embedding) VALUES($1,$2,0,'boundary evidence','[1,0,0,0,0,0,0,0]')",document,node)
            nodes.append(node);documents.append(document)
        for other in nodes[1:]:
            await pool.execute("INSERT INTO gcor.edges(source_id,target_id,relation) VALUES($1,$2,'RELATES_TO')",nodes[0],other)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(main.app),base_url='http://integration') as client:
            headers={'Authorization':'Bearer '+TOKEN}
            for path in ['/api/sessions','/api/recovery/records','/api/knowledge/approve']:
                assert (await client.post(path,headers=headers)).status_code == 403
            response=await client.post('/api/ask',headers=headers,json={'query':'boundary','channel_id':'boundary-b'})
            assert response.status_code == 403,response.text
            for broken_embedding in [False,True]:
                embedding=AsyncMock(side_effect=RuntimeError('inference unavailable')) if broken_embedding else AsyncMock(return_value=[[1,0,0,0,0,0,0,0]])
                with patch.object(main,'embed',embedding),patch.object(main,'GENERATION_MODEL',''),patch.object(main,'graph_candidates',AsyncMock(return_value=documents)):
                    response=await client.post('/api/ask',headers=headers,json={'query':'boundary','approved_only':False,'hops':3})
                assert response.status_code == 200,response.text
                result=response.json()
                assert len(result['chunks']) == 1,result
                assert result['chunks'][0]['node_id'] == str(nodes[0]),result
                assert {n['id'] for n in result['graph_nodes']} == {str(nodes[0])},result
                assert len(result['citations']) == 1,result
                assert '[1]' in result['answer'],result
            assert (await client.post('/api/retrieve',headers={'X-Gcor-Webhook-Secret':main.STACK_API_SECRET},json={'query':'boundary'})).status_code == 401
    print('PASS: real database channel/state isolation, traversal restrictions, no secret fallback, lexical outage recovery')


asyncio.run(run())
