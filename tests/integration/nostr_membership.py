"""Synthetic Buzz tables and signed requests, isolated integration database only."""
import asyncio
import json
import os
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import httpx
from coincurve import PrivateKey

assert os.environ['POSTGRES_DB']=='integration', 'Only run on disposable integration storage'
os.environ['GCOR_ACCESS_MODE']='buzz'
os.environ['GCOR_PUBLIC_ORIGIN']='http://test'
import main
from tests.integration.admin_db import admin_pool
from tests.test_enterprise_access import signed_event


async def run():
    async with main.lifespan(main.app):
        p=main.app.state.pool
        admin=await admin_pool()
        # Mirror the fields inspected in the installed Buzz 0.2.1 schema.
        await admin.execute('''
            CREATE TABLE IF NOT EXISTS public.channels(id uuid PRIMARY KEY,community_id uuid,name text DEFAULT 'Test',visibility text,deleted_at timestamptz,archived_at timestamptz);
            CREATE TABLE IF NOT EXISTS public.users(community_id uuid,pubkey bytea,deactivated_at timestamptz);
            CREATE TABLE IF NOT EXISTS public.channel_members(community_id uuid,channel_id uuid,pubkey bytea,role text DEFAULT 'member',removed_at timestamptz);
        ''')
        key=PrivateKey(); stranger=PrivateKey(); channel=uuid4(); community=uuid4()
        pubkey=key.public_key_xonly.format()
        await admin.execute("INSERT INTO public.channels(id,community_id,visibility) VALUES($1,$2,'private')",channel,community)
        await admin.execute('INSERT INTO public.users(community_id,pubkey) VALUES($1,$2)',community,pubkey)
        await admin.execute('INSERT INTO public.channel_members(community_id,channel_id,pubkey) VALUES($1,$2,$3)',community,channel,pubkey)
        body=json.dumps({'query':'evidence','channel_id':str(channel)}).encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(main.app),base_url='http://test') as client:
            async def ask(signer=key,raw=body,auth_body=body):
                return await client.post('/api/ask',content=raw,headers={'Content-Type':'application/json',
                    'Authorization':'Nostr '+signed_event(signer,auth_body).decode()})
            with patch.object(main,'embed',AsyncMock(return_value=[[1,0,0,0,0,0,0,0]])),patch.object(main,'GENERATION_MODEL',''):
                response=await ask()
                assert response.status_code==200,response.text
                assert (await ask(stranger)).status_code==403
                assert (await ask(raw=body+b' ')).status_code==401
                await admin.execute('UPDATE public.channel_members SET removed_at=now() WHERE channel_id=$1',channel)
                assert (await ask()).status_code==403
                await admin.execute('UPDATE public.channel_members SET removed_at=NULL WHERE channel_id=$1',channel)
                await admin.execute('UPDATE public.users SET deactivated_at=now() WHERE pubkey=$1',pubkey)
                assert (await ask()).status_code==403
                await admin.execute('UPDATE public.users SET deactivated_at=NULL WHERE pubkey=$1',pubkey)
                await admin.execute('UPDATE public.channels SET deleted_at=now() WHERE id=$1',channel)
                assert (await ask()).status_code==403
                response=await client.post('/api/ask',content=body,headers={'X-Gcor-Webhook-Secret':main.STACK_API_SECRET})
                assert response.status_code==401
    print('PASS: signed Buzz identity, membership/deactivation/deletion revocation, body tampering, no shared-secret fallback')


asyncio.run(run())
