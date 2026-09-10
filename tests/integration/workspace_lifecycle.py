"""Signed contributor -> durable job -> admin review, on disposable DB only."""
import asyncio
import json
import os
from datetime import datetime,timezone,timedelta
from uuid import uuid4

import httpx
from coincurve import PrivateKey

assert os.environ['POSTGRES_DB']=='integration'
os.environ['GCOR_ACCESS_MODE']='buzz';os.environ['GCOR_PUBLIC_ORIGIN']='http://test'
import main
from tests.integration.admin_db import admin_pool
from tests.test_enterprise_access import signed_event
from enterprise_workflows import process_one


async def run():
    async with main.lifespan(main.app):
        pool=main.app.state.pool
        admin=await admin_pool()
        key=PrivateKey();pubkey=key.public_key_xonly.format();channel=uuid4();community=uuid4()
        await admin.execute("INSERT INTO public.channels(id,community_id,visibility) VALUES($1,$2,'private')",channel,community)
        await admin.execute('INSERT INTO public.users(community_id,pubkey) VALUES($1,$2)',community,pubkey)
        await admin.execute("INSERT INTO public.channel_members(community_id,channel_id,pubkey,role) VALUES($1,$2,$3,'member')",community,channel,pubkey)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(main.app),base_url='http://test') as client:
            async def call(path,payload={},signer=key):
                body=json.dumps({'channel_id':str(channel),**payload}).encode()
                return await client.post(path,content=body,headers={'Content-Type':'application/json',
                    'Authorization':'Nostr '+signed_event(signer,body,url='http://test'+path).decode()})
            request={'request_id':str(uuid4()),'title':'Workspace policy','text':'Release checklist '+str(uuid4())}
            first=await call('/api/workspace/submit',request);assert first.status_code==202,first.text
            again=await call('/api/workspace/submit',request);assert first.json()['id']==again.json()['id']
            assert (await call('/api/workspace/submit',request|{'text':'different'})).status_code==409
            job_id=UUID(first.json()['id'])
            for _ in range(25):
                await process_one(main.app)
                job=await pool.fetchrow('SELECT status,result,error FROM gcor.ingestion_jobs WHERE id=$1',job_id)
                if job['status']=='completed':break
                assert job['status']!='failed',dict(job)
                await asyncio.sleep(1)
            assert job['status']=='completed',dict(job)
            result=json.loads(job['result']);doc_id=result['document_id']
            info=await call('/api/workspace/detail',{'document_id':doc_id});assert info.status_code==200,info.text
            data=info.json()['document']
            review={'document_id':doc_id,'request_id':str(uuid4()),'expected_updated_at':data['updated_at'],
                'state':'approved','readers':[],'owner_pubkey':pubkey.hex(),'review_due':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()}
            assert (await call('/api/workspace/review',review)).status_code==403
            await admin.execute("UPDATE public.channel_members SET role='admin' WHERE channel_id=$1",channel)
            approved=await call('/api/workspace/review',review);assert approved.status_code==200,approved.text
            assert (await call('/api/workspace/review',review)).json()==approved.json()
            stale=await call('/api/workspace/review',review|{'request_id':str(uuid4()),'state':'rejected'})
            assert stale.status_code==409,stale.text
            cross=await call('/api/workspace/detail',{'document_id':str(uuid4())});assert cross.status_code==404
            feedback={'document_id':doc_id,'request_id':str(uuid4()),'category':'helpful','note':'Verified source'}
            assert (await call('/api/workspace/feedback',feedback)).status_code==200
            detail=(await call('/api/workspace/detail',{'document_id':doc_id})).json()
            assert len(detail['history'])==1 and detail['history'][0]['payload']['actor']==pubkey.hex()
            assert len(detail['feedback'])==1
            other=PrivateKey();other_pub=other.public_key_xonly.format()
            await admin.execute('INSERT INTO public.users(community_id,pubkey) VALUES($1,$2)',community,other_pub)
            await admin.execute("INSERT INTO public.channel_members(community_id,channel_id,pubkey,role) VALUES($1,$2,$3,'member')",community,channel,other_pub)
            assert (await call('/api/workspace/detail',{'document_id':doc_id},other)).status_code==404
            denied=await call('/api/ask',{'query':'Release checklist'},other)
            assert denied.status_code==200 and not denied.json()['chunks'],denied.text
            # Contributor replay of identical bytes must not reset the approved document.
            duplicate=await call('/api/workspace/submit',request|{'request_id':str(uuid4())})
            for _ in range(25):
                await process_one(main.app)
                status=await pool.fetchval('SELECT status FROM gcor.ingestion_jobs WHERE id=$1',UUID(duplicate.json()['id']))
                if status=='completed':break
                await asyncio.sleep(1)
            state=await pool.fetchval("SELECT metadata->>'knowledge_state' FROM gcor.documents WHERE id=$1",UUID(doc_id))
            assert state=='approved',state
    print('PASS: signed role separation, durable/idempotent ingestion, review concurrency, verified actor audit, duplicate governance preservation')


from uuid import UUID
asyncio.run(run())
