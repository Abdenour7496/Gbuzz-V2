"""Real storage chat-command lifecycle; isolated database and synthetic signatures only."""
import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4, UUID
from unittest.mock import AsyncMock, patch
from coincurve import PrivateKey

assert os.environ['POSTGRES_DB']=='integration'
os.environ['GCOR_INGESTION_WORKER']='false'
import main
from tests.integration.admin_db import admin_pool
from buzz_chat import execute, sign_event, cycle


async def run():
    async with main.lifespan(main.app):
        pool=main.app.state.pool
        await pool.execute('UPDATE gcor.buzz_knowledge_channels SET enabled=false')
        admin=await admin_pool()
        await admin.execute('''
            CREATE TABLE IF NOT EXISTS public.channels(id uuid PRIMARY KEY,community_id uuid,name text,visibility text,deleted_at timestamptz,archived_at timestamptz);
            CREATE TABLE IF NOT EXISTS public.users(community_id uuid,pubkey bytea,deactivated_at timestamptz);
            ALTER TABLE public.users ADD COLUMN IF NOT EXISTS agent_type text;
            ALTER TABLE public.users ADD COLUMN IF NOT EXISTS agent_owner_pubkey bytea;
            CREATE TABLE IF NOT EXISTS public.channel_members(community_id uuid,channel_id uuid,pubkey bytea,role text,removed_at timestamptz);
            CREATE TABLE IF NOT EXISTS public.events(id bytea PRIMARY KEY,pubkey bytea,kind integer,tags jsonb,content text,sig bytea,created_at timestamptz,received_at timestamptz DEFAULT now(),channel_id uuid,deleted_at timestamptz);
        ''')
        channel=uuid4();community=uuid4();owner=PrivateKey();member=PrivateKey();bot=PrivateKey()
        await admin.execute("INSERT INTO public.channels(id,community_id,name,visibility) VALUES($1,$2,'Knowledge test','private')",channel,community)
        for key,role,agent in [(owner,'owner',None),(member,'member',None),(bot,'bot','knowledge')]:
            pub=key.public_key_xonly.format()
            await admin.execute('INSERT INTO public.users(community_id,pubkey,agent_type,agent_owner_pubkey) VALUES($1,$2,$3,$4)',community,pub,agent,owner.public_key_xonly.format() if agent else None)
            await admin.execute('INSERT INTO public.channel_members(community_id,channel_id,pubkey,role) VALUES($1,$2,$3,$4)',community,channel,pub,role)
        await pool.execute('INSERT INTO gcor.buzz_knowledge_channels(channel_id) VALUES($1)',channel)
        async def message(key,content):
            e=sign_event(key,9,[['h',str(channel)]],content)
            dt=datetime.fromtimestamp(e['created_at'],timezone.utc)
            await admin.execute('INSERT INTO public.events(id,pubkey,kind,tags,content,sig,created_at,channel_id) VALUES($1,$2,9,$3::jsonb,$4,$5,$6,$7)',
                bytes.fromhex(e['id']),bytes.fromhex(e['pubkey']),json.dumps(e['tags']),content,bytes.fromhex(e['sig']),dt,channel)
            return dict(event_id=e['id'],author=e['pubkey'],kind=9,tags=e['tags'],content=content,signature=e['sig'],created_at=dt,channel_id=channel)
        with patch.object(main,'GENERATION_MODEL',''):
            source=await message(member,'Decision: use weekly recovery drills to verify restoration of knowledge.')
            synth=await message(member,'!knowledge synthesize '+source['event_id'])
            # Exercise inbox, command execution, reply persistence and exactly reused delivery ID.
            publisher=AsyncMock()
            with patch('buzz_chat.publish',publisher):
                await cycle(main.app,bot)
                self_reply=publisher.call_args.args[1]
                assert 'Proposal saved:' in self_reply['content'],self_reply
                assert self_reply['tags'][0]==['h',str(channel)]
                await cycle(main.app,bot);assert publisher.call_count==1
            doc=await pool.fetchrow("SELECT id,metadata,updated_at FROM gcor.documents WHERE metadata->>'buzz_command_id'=$1",synth['event_id'])
            assert doc and json.loads(doc['metadata'])['knowledge_state']=='proposed'
            doc_id=str(doc['id']);revision=doc['updated_at'].isoformat()
            denied=await message(member,f'!knowledge approve {doc_id} {revision}')
            try:await execute(main.app,denied);raise AssertionError('Member approved')
            except __import__('fastapi').HTTPException as e:assert e.status_code==403
            denied_bot=await message(bot,f'!knowledge approve {doc_id} {revision}')
            try:await execute(main.app,denied_bot);raise AssertionError('Bot approved')
            except __import__('fastapi').HTTPException as e:assert e.status_code==403
            approved=await message(owner,f'!knowledge approve {doc_id} {revision}')
            assert 'recorded' in await execute(main.app,approved)
            assert 'recorded' in await execute(main.app,approved) # idempotent review retry
            answer=await execute(main.app,await message(member,'!knowledge ask weekly recovery drills'))
            assert 'recovery' in answer.lower(),answer
            # Save a cited answer without promoting the generated reply to independent evidence.
            ask_row=await message(member,'!knowledge ask How do weekly recovery drills work?')
            await pool.execute('INSERT INTO gcor.buzz_knowledge_commands(event_id,channel_id) VALUES($1,$2)',ask_row['event_id'],channel)
            answer=await execute(main.app,ask_row)
            reply=sign_event(bot,9,[['h',str(channel)]],str(answer))
            await pool.execute('UPDATE gcor.buzz_knowledge_commands SET response=$2::jsonb,delivered_at=now() WHERE event_id=$1',ask_row['event_id'],json.dumps(reply))
            saved=await execute(main.app,await message(member,'!knowledge save '+reply['id']))
            saved_id=saved.card['document_id'];saved_revision=saved.card['revision']
            await execute(main.app,await message(owner,f'!knowledge approve {saved_id} {saved_revision}'))
            # Read-time evidence validity: with the invalidation worker idle, removing the
            # original discussion message must immediately hide the proposal built on it and
            # the saved answer that depends on that proposal (two hops), while both rows still
            # read 'approved' in storage.
            question='What is the weekly recovery drill decision?'
            cited=str(await execute(main.app,await message(member,'!knowledge ask '+question+' (before removal)')))
            assert 'buzz://knowledge/' in cited and 'Discussion findings' in cited,cited
            await admin.execute('UPDATE public.events SET deleted_at=now() WHERE id=$1',bytes.fromhex(source['event_id']))
            try:
                states=await pool.fetch("SELECT metadata->>'knowledge_state' AS s FROM gcor.documents WHERE id=ANY($1::uuid[])",[doc['id'],UUID(saved_id)])
                assert {r['s'] for r in states}=={'approved'},states
                hidden=str(await execute(main.app,await message(member,'!knowledge ask '+question+' (after removal)')))
                assert 'buzz://knowledge/' not in hidden and 'Discussion findings' not in hidden and 'Answer:' not in hidden,hidden
                for hop in (doc['id'],UUID(saved_id)):
                    assert not await pool.fetchval('SELECT gcor.knowledge_evidence_current($1)',hop)
                current=(await pool.fetchval('SELECT updated_at FROM gcor.documents WHERE id=$1',doc['id'])).isoformat()
                try:
                    await execute(main.app,await message(member,f'!knowledge revise {doc_id} {current} | Stale base must not be revisable.'))
                    raise AssertionError('Revision accepted on withdrawn evidence')
                except __import__('fastapi').HTTPException as e:assert e.status_code==409
            finally:
                await admin.execute('UPDATE public.events SET deleted_at=NULL WHERE id=$1',bytes.fromhex(source['event_id']))
            # Competing revisions must never both replace the same approved base.
            base=await pool.fetchrow('SELECT updated_at FROM gcor.documents WHERE id=$1',doc['id'])
            base_revision=base['updated_at'].isoformat()
            revision_one=await execute(main.app,await message(member,f'!knowledge revise {doc_id} {base_revision} | Run recovery drills every week and record results.'))
            revision_two=await execute(main.app,await message(member,f'!knowledge revise {doc_id} {base_revision} | Run recovery drills daily.'))
            changes=await execute(main.app,await message(member,'!knowledge changes '+revision_one.card['document_id']))
            assert 'Proposed version' in changes,changes
            await execute(main.app,await message(owner,f'!knowledge approve {revision_one.card["document_id"]} {revision_one.card["revision"]}'))
            try:
                await execute(main.app,await message(owner,f'!knowledge approve {revision_two.card["document_id"]} {revision_two.card["revision"]}'))
                raise AssertionError('Stale competing revision approved')
            except __import__('fastapi').HTTPException as e:assert e.status_code==409
            old=await pool.fetchval("SELECT metadata->>'knowledge_state' FROM gcor.documents WHERE id=$1",doc['id'])
            assert old=='superseded'
            from buzz_chat import invalidate_removed_evidence
            await invalidate_removed_evidence(main.app)
            assert await pool.fetchval("SELECT metadata->>'knowledge_state' FROM gcor.documents WHERE id=$1",UUID(saved_id))=='proposed'
            assert 'superseded' in await execute(main.app,await message(member,'!knowledge history '+doc_id))
            assert 'Checked' in await execute(main.app,await message(member,'!knowledge lint'))
            assert 'Knowledge index' == (await execute(main.app,await message(member,'!knowledge index'))).card['title']
            # Continue ACL checks against the new approved revision.
            doc=await pool.fetchrow('SELECT id FROM gcor.documents WHERE id=$1',UUID(revision_one.card['document_id']))
            await pool.execute("UPDATE gcor.documents SET metadata=metadata||jsonb_build_object('knowledge_readers',jsonb_build_array($2::text)) WHERE id=$1",doc['id'],member.public_key_xonly.format().hex())
            answer=await execute(main.app,await message(member,'!knowledge ask weekly recovery drills again'))
            assert 'buzz://knowledge/' not in answer,answer
            await admin.execute('UPDATE public.channel_members SET removed_at=now() WHERE channel_id=$1 AND pubkey=$2',channel,member.public_key_xonly.format())
            try:await execute(main.app,await message(member,'!knowledge list'));raise AssertionError('Revoked member allowed')
            except PermissionError:pass
        print('Chat lifecycle passed: signed discussion, AI proposal, role checks, human approval, answer, read-time evidence validity, replay, ACL and revocation.')


if __name__=='__main__':asyncio.run(run())
