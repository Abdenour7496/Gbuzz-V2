"""Database-backed tenant and workload boundary checks for relationship projection."""
import asyncio
import os
import uuid

import asyncpg


async def connect(user, password):
    return await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"), database=os.getenv("POSTGRES_DB", "integration"),
        user=user, password=password,
    )


async def main():
    admin = await connect("integration", "integration")
    runtime = await connect("gcor_runtime", "runtime-secret")
    projector = await connect("relationship_integration", "relationship-secret")
    document_id = uuid.uuid4()
    authoritative_node_id = uuid.uuid4()
    try:
        await admin.execute(
            """INSERT INTO gcor.documents(id,content_sha256,identity_sha256,title,object_key,access_level,metadata)
               VALUES($1,$2,$3,'relationship rls','test','private',$4::jsonb)""",
            document_id, "f" * 64, "e" * 64, '{"channel_id":"secret-channel","knowledge_state":"approved"}',
        )
        await admin.execute(
            """INSERT INTO gcor.relationship_projection(document_id,source_revision,channel_id,status,model)
               VALUES($1,$2,'secret-channel','pending','test')""", document_id, "f" * 64,
        )
        await admin.execute(
            """INSERT INTO gcor.nodes(id,node_type,label,content,access_level,properties)
               VALUES($1,'Concept','authoritative','must-not-change','private','{}'::jsonb)""",
            authoritative_node_id,
        )
        await runtime.execute("RESET gcor.channel_id")
        await runtime.execute("RESET gcor.workload")
        assert await runtime.fetchval("SELECT count(*) FROM gcor.relationship_projection") == 0
        await projector.execute("SELECT set_config('gcor.workload','relationship-projector',false)")
        assert await projector.fetchval("SELECT count(*) FROM gcor.relationship_projection WHERE document_id=$1", document_id) == 1
        assert await projector.fetchval("SELECT count(*) FROM gcor.documents WHERE id=$1", document_id) == 1
        assert await projector.execute(
            "UPDATE gcor.nodes SET content='mutated' WHERE id=$1", authoritative_node_id,
        ) == "UPDATE 0"
        assert await admin.fetchval("SELECT content FROM gcor.nodes WHERE id=$1", authoritative_node_id) == "must-not-change"
        try:
            await projector.execute("DELETE FROM gcor.nodes WHERE id=$1", authoritative_node_id)
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("relationship role can delete graph rows")
        try:
            await projector.fetchval("SELECT count(*) FROM gcor.audit_pack_exports")
        except asyncpg.InsufficientPrivilegeError:
            pass
        else:
            raise AssertionError("relationship role read unrelated audit exports")
    finally:
        await projector.close()
        await runtime.close()
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
