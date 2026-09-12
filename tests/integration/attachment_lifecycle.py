import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

import asyncpg

sys.path.insert(0, str(Path('/workspace/projector')))
import main as projector


async def run():
    pool=await asyncpg.create_pool(host='postgres',user='integration',password='integration',database='integration')
    channel=uuid4();old_event=uuid4().hex*2;current_event=uuid4().hex*2
    try:
        await pool.execute("CREATE TABLE IF NOT EXISTS public.channels(id uuid PRIMARY KEY,name text,visibility text,deleted_at timestamptz,archived_at timestamptz)")
        await pool.execute("CREATE TABLE IF NOT EXISTS public.events(id bytea PRIMARY KEY,channel_id uuid,deleted_at timestamptz)")
        await pool.execute("INSERT INTO public.channels(id,name,visibility) VALUES($1,'Test','private')",channel)
        await pool.execute("INSERT INTO public.events(id,channel_id,deleted_at) VALUES(decode($1,'hex'),$3,now()),(decode($2,'hex'),$3,NULL)",old_event,current_event,channel)
        ids=[]
        for event,version,stamp in [(old_event,'v1',datetime(2000,1,1,tzinfo=timezone.utc)),(current_event,'v2',datetime(2100,1,1,tzinfo=timezone.utc))]:
            doc=await pool.fetchval("""INSERT INTO gcor.documents(content_sha256,identity_sha256,title,source_uri,object_key,access_level,metadata,updated_at)
                VALUES($1,$2,'Timesheet','buzz://event/source#attachment:1','object','private',$3::jsonb,$4::timestamptz) RETURNING id""",
                event,event,json.dumps({'record_type':'buzz_attachment','parent_event_id':event,'channel_id':str(channel),'extraction_version':version}),stamp)
            node=await pool.fetchval("INSERT INTO gcor.nodes(document_id,node_type,label,content,access_level) VALUES($1,'Chunk','chunk','Martin Bottos 78 hours','private') RETURNING id",doc)
            await pool.execute("INSERT INTO gcor.chunks(document_id,node_id,ordinal,content,embedding) VALUES($1,$2,0,'Martin Bottos 78 hours',$3::vector)",doc,node,'[0,0,0,0,0,0,0,0]')
            ids.append(doc)
        await projector.invalidate_stale_evidence(pool)
        assert await pool.fetchval('SELECT count(*) FROM gcor.chunks WHERE document_id=$1',ids[0])==0
        assert await pool.fetchval('SELECT count(*) FROM gcor.nodes WHERE document_id=$1',ids[0])==0
        assert await pool.fetchval('SELECT count(*) FROM gcor.chunks WHERE document_id=$1',ids[1])==1
        assert await pool.fetchval('SELECT count(*) FROM gcor.documents WHERE id=$1',ids[0])==1
        await projector.invalidate_stale_evidence(pool)  # restart/reconciliation idempotency
        assert await pool.fetchval('SELECT count(*) FROM gcor.chunks WHERE document_id=$1',ids[1])==1
        print('attachment lifecycle reconciliation passed')
    finally:
        await pool.close()


asyncio.run(run())
