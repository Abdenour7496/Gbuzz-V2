"""Row-level security holds even when application filters are omitted.

Runs as the restricted runtime role against the isolated integration database. Rows are
seeded by the owner role; every assertion below issues deliberately unfiltered SQL
through the same ScopedPool the services use.
"""
import asyncio
import json
import os
from uuid import uuid4

import asyncpg

import main
from access_policy import Principal, current_principal
from db_scope import ScopedPool
from tests.integration.admin_db import admin_pool


async def run():
    assert os.environ['POSTGRES_DB'] == 'integration'
    admin = await admin_pool()
    runtime = await asyncpg.create_pool(host=main.POSTGRES_HOST, port=main.POSTGRES_PORT, user=main.POSTGRES_USER,
                                        password=main.POSTGRES_PASSWORD, database=main.POSTGRES_DB, min_size=1, max_size=1)
    role = await runtime.fetchrow('SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = current_user')
    assert not any(role.values()), f'runtime role is privileged: {dict(role)}'
    assert await runtime.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = 'gcor.documents'::regclass"), 'RLS not enabled'

    pool = ScopedPool(runtime)
    dims = await runtime.fetchval("SELECT atttypmod FROM pg_attribute WHERE attrelid='gcor.chunks'::regclass AND attname='embedding'")
    channel_a, channel_b = str(uuid4()), str(uuid4())
    docs = {}
    try:
        for channel in (channel_a, channel_b):
            docs[channel] = await admin.fetchval(
                "INSERT INTO gcor.documents(content_sha256,identity_sha256,title,object_key,access_level,metadata) "
                "VALUES($1,$1,'rls probe','rls/probe','public',$2::jsonb) RETURNING id",
                'rls-' + channel, json.dumps({'channel_id': channel, 'knowledge_state': 'approved'}))
            node = await admin.fetchval("INSERT INTO gcor.nodes(document_id,node_type,label,content) VALUES($1,'Chunk','rls','rls') RETURNING id", docs[channel])
            await admin.execute("INSERT INTO gcor.chunks(document_id,node_id,ordinal,content,embedding) VALUES($1,$2,0,'rls',$3::vector)",
                                docs[channel], node, '[' + ','.join(['0.1'] * dims) + ']')
        both = "SELECT count(*) FROM gcor.documents WHERE id = ANY($1::uuid[])"
        ids = list(docs.values())
        assert await pool.fetchval(both, ids) == 2, 'unscoped runtime connection must see every row'

        token = current_principal.set(Principal('npub-probe', channel_a, 'public'))
        try:
            assert await pool.fetchval(both, ids) == 1, 'scoped query leaked another channel'
            assert await pool.fetchval("SELECT count(*) FROM gcor.chunks WHERE document_id=$1", docs[channel_b]) == 0
            assert await pool.fetchval("SELECT count(*) FROM gcor.nodes WHERE document_id=$1", docs[channel_b]) == 0
            assert await pool.execute("UPDATE gcor.documents SET title='tampered' WHERE id=$1", docs[channel_b]) == 'UPDATE 0'
            try:
                await pool.execute("INSERT INTO gcor.documents(content_sha256,identity_sha256,title,object_key,metadata) "
                                   "VALUES('rls-leak','rls-leak','leak','rls/leak',$1::jsonb)", json.dumps({'channel_id': channel_b}))
                raise AssertionError('scoped connection wrote into another channel')
            except asyncpg.InsufficientPrivilegeError:
                pass
            # A different access level in the same channel is outside scope too.
            current_principal.set(Principal('npub-probe', channel_a, 'restricted'))
            assert await pool.fetchval(both, ids) == 0
        finally:
            current_principal.reset(token)
        # The single pooled connection was reused: scope must not survive release.
        assert await pool.fetchval("SELECT current_setting('gcor.channel_id', true)") in ('', None)
        assert await pool.fetchval(both, ids) == 2
        assert await admin.fetchval("SELECT title FROM gcor.documents WHERE id=$1", docs[channel_b]) == 'rls probe'
    finally:
        await admin.execute("DELETE FROM gcor.documents WHERE id = ANY($1::uuid[])", list(docs.values()))
        await runtime.close()
        await admin.close()
    print('PASS: runtime role is unprivileged, RLS scopes documents/chunks/nodes to the identity channel and access level, writes outside scope are refused, scope resets on release')


if __name__ == '__main__':
    asyncio.run(run())
