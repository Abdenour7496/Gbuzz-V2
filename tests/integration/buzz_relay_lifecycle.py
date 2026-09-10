"""Actual Buzz 0.2.1 WebSocket publication and knowledge loop, disposable database only."""
import asyncio
import json
import os
from uuid import uuid4
from unittest.mock import patch
from coincurve import PrivateKey

assert os.environ['POSTGRES_DB']=='integration_relay'
os.environ['GCOR_INGESTION_WORKER']='false'
os.environ['BUZZ_KNOWLEDGE_RELAY_URL']='ws://chat-relay:3000'
os.environ['BUZZ_KNOWLEDGE_AUTH_URL']='ws://chat-relay:3000'
import main
from tests.integration.admin_db import admin_pool
from buzz_chat import sign_event,publish,cycle,invalidate_removed_evidence


async def run():
    async with main.lifespan(main.app):
        p=main.app.state.pool;owner=PrivateKey();member=PrivateKey();bot=PrivateKey();channel=uuid4()
        admin=await admin_pool()
        community=await p.fetchval("SELECT id FROM public.communities WHERE host='chat-relay:3000'")
        assert community
        for key,agent in [(owner,None),(member,None),(bot,'gcor-knowledge')]:
            await admin.execute('INSERT INTO public.users(community_id,pubkey,agent_type,agent_owner_pubkey) VALUES($1,$2,$3,$4)',
                community,key.public_key_xonly.format(),agent,owner.public_key_xonly.format() if agent else None)
        await admin.execute("INSERT INTO public.channels(id,community_id,name,visibility,created_by) VALUES($1,$2,'Knowledge protocol test','private',$3)",channel,community,owner.public_key_xonly.format())
        for key,role in [(owner,'owner'),(member,'member'),(bot,'bot')]:
            await admin.execute('INSERT INTO public.channel_members(community_id,channel_id,pubkey,role) VALUES($1,$2,$3,$4::member_role)',community,channel,key.public_key_xonly.format(),role)
        await p.execute('INSERT INTO gcor.buzz_knowledge_channels(channel_id) VALUES($1)',channel)
        async def send(key,text):
            event=sign_event(key,9,[['h',str(channel)]],text)
            await publish(key,event)
            assert await p.fetchval('SELECT 1 FROM public.events WHERE id=$1',bytes.fromhex(event['id']))
            return event
        async def command(key,text):
            event=await send(key,text)
            await cycle(main.app,bot)
            result=await p.fetchrow('SELECT response,delivered_at,error FROM gcor.buzz_knowledge_commands WHERE event_id=$1',event['id'])
            assert result and result['delivered_at'],dict(result) if result else 'No inbox row'
            reply=json.loads(result['response'])
            assert await p.fetchval('SELECT 1 FROM public.events WHERE id=$1 AND channel_id=$2',bytes.fromhex(reply['id']),channel)
            return reply['content']
        with patch.object(main,'GENERATION_MODEL',''):
            discussion=await send(member,'Our team decided to run weekly restore drills and record checksum evidence.')
            proposal=await command(member,'!knowledge synthesize')
            assert 'Proposal saved:' in proposal,proposal
            doc=await p.fetchrow("SELECT id,updated_at FROM gcor.documents WHERE metadata->>'channel_id'=$1 ORDER BY created_at DESC LIMIT 1",str(channel))
            denied=await command(member,f'!knowledge approve {doc["id"]} {doc["updated_at"].isoformat()}')
            assert 'Human channel owner/admin required' in denied,denied
            approved=await command(owner,f'!knowledge approve {doc["id"]} {doc["updated_at"].isoformat()}')
            assert 'recorded' in approved,approved
            answer=await command(member,'!knowledge ask weekly restore drills')
            assert 'buzz://knowledge/' in answer,answer
            # Source deletion withdraws approval with an audit/outbox record.
            await admin.execute('UPDATE public.events SET deleted_at=now() WHERE id=$1',bytes.fromhex(discussion['id']))
            await invalidate_removed_evidence(main.app)
            assert await p.fetchval("SELECT metadata->>'knowledge_state' FROM gcor.documents WHERE id=$1",doc['id'])=='proposed'
        print('REAL BUZZ RELAY PASS: signed messages, threaded agent replies, synthesis, human approval, retrieval, evidence deletion.')


if __name__=='__main__':asyncio.run(run())
