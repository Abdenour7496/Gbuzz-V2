"""Enroll the dedicated Knowledge agent in one explicitly selected Buzz channel."""
import asyncio
import os
import sys
from uuid import UUID
from coincurve import PrivateKey
import main


async def run():
    channel=UUID(sys.argv[1])
    key=PrivateKey(bytes.fromhex(os.environ['BUZZ_KNOWLEDGE_PRIVATE_KEY']))
    pubkey=key.public_key_xonly.format()
    pool=await __import__('asyncpg').create_pool(host=main.POSTGRES_HOST,port=main.POSTGRES_PORT,
        user=main.POSTGRES_USER,password=main.POSTGRES_PASSWORD,database=main.POSTGRES_DB)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row=await conn.fetchrow('''SELECT c.community_id,m.pubkey FROM public.channels c
                    JOIN public.channel_members m ON m.channel_id=c.id AND m.community_id=c.community_id
                    JOIN public.users u ON u.community_id=c.community_id AND u.pubkey=m.pubkey
                    WHERE c.id=$1 AND c.deleted_at IS NULL AND c.archived_at IS NULL
                    AND m.role::text='owner' AND m.removed_at IS NULL AND u.deactivated_at IS NULL
                    AND u.agent_type IS NULL ORDER BY m.joined_at LIMIT 1''',channel)
                if not row:raise RuntimeError('Channel requires an active human owner')
                await conn.execute('''INSERT INTO public.users(community_id,pubkey,display_name,agent_type,agent_owner_pubkey,about)
                    VALUES($1,$2,'Knowledge','gcor-knowledge',$3,'Governed knowledge proposals and answers. Send !knowledge help.')
                    ON CONFLICT(community_id,pubkey) DO NOTHING''',row['community_id'],pubkey,row['pubkey'])
                await conn.execute('''INSERT INTO public.channel_members(community_id,channel_id,pubkey,role,invited_by)
                    VALUES($1,$2,$3,'bot',$4) ON CONFLICT(community_id,channel_id,pubkey) DO NOTHING''',row['community_id'],channel,pubkey,row['pubkey'])
                await conn.execute('INSERT INTO gcor.buzz_knowledge_channels(channel_id) VALUES($1) ON CONFLICT DO NOTHING',channel)
        print('Knowledge agent enrolled; existing users and permissions preserved. Public key: '+pubkey.hex())
    finally:await pool.close()


if __name__=='__main__':asyncio.run(run())
