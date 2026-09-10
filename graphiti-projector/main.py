import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


POSTGRES = {
    "host": os.getenv("POSTGRES_HOST", "postgres"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
    "database": os.environ["POSTGRES_DB"],
}
GRAPHITI_MCP_URL = os.getenv("GRAPHITI_MCP_URL", "http://graphiti-mcp:8000/mcp")
GRAPHITI_MCP_HOST_HEADER = os.getenv("GRAPHITI_MCP_HOST_HEADER", "localhost:8000")
POLL_SECONDS = float(os.getenv("GRAPHITI_PROJECTOR_POLL_SECONDS", "2"))
MAX_ATTEMPTS = int(os.getenv("GRAPHITI_PROJECTOR_MAX_ATTEMPTS", "8"))
PROCESSING_TIMEOUT_MINUTES = int(os.getenv("GRAPHITI_PROJECTOR_PROCESSING_TIMEOUT_MINUTES", "15"))
SUBMISSION_TIMEOUT_MINUTES = int(os.getenv("GRAPHITI_PROJECTOR_SUBMISSION_TIMEOUT_MINUTES", "45"))


def projection_bucket(status: str, attempts: int, reconciled: bool, max_attempts: int = MAX_ATTEMPTS) -> str | None:
    """Return the release-gate bucket for an unfinished projection row."""
    if status == "pending":
        return "pending"
    if status == "processing":
        return "dead_letter" if attempts >= max_attempts else "in_flight"
    if status == "submitted" and not reconciled:
        return "in_flight"
    if status == "failed":
        return "dead_letter" if attempts >= max_attempts else "retryable"
    return None


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def render_error(error: BaseException) -> str:
    nested = getattr(error, "exceptions", None)
    if nested:
        return f"{error}: " + " | ".join(render_error(item) for item in nested)
    return f"{type(error).__name__}: {error}"


def build_episode_body(row: dict[str, Any]) -> str:
    metadata = json_object(row.get("metadata"))
    body = {
        "schema": "https://gbuzz.local/schemas/session-knowledge-entry.schema.json",
        "schema_version": "1.0.0",
        "session": {
            "id": str(row["session_id"]),
            "external_id": row["external_session_id"],
            "channel_id": row.get("channel_id"),
            "channel_name": row.get("channel_name"),
        },
        "participant": {
            "id": str(row["participant_id"]),
            "type": row["participant_type"],
            "external_id": row["participant_external_id"],
            "name": row["participant_name"],
        },
        "entry": {
            "id": str(row["entry_id"]),
            "external_id": row["entry_external_id"],
            "type": row["entry_type"],
            "occurred_at": row["occurred_at"].isoformat(),
            "content": row["content"],
        },
        "provenance": {
            "postgres_document_id": str(row["source_document_id"]) if row.get("source_document_id") else None,
            "postgres_record_id": str(row["source_record_id"]),
            "bucket": metadata.get("bucket"),
            "original_key": metadata.get("original_key"),
            "markdown_key": metadata.get("markdown_key"),
            "record_key": metadata.get("record_key"),
            "source_uri": metadata.get("source_uri"),
        },
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def choose_ingest_tool(tools: list[Any]) -> tuple[str, set[str]]:
    by_name = {tool.name: tool for tool in tools}
    for name in ("add_memory", "add_episode"):
        if name in by_name:
            schema = by_name[name].inputSchema or {}
            return name, set((schema.get("properties") or {}).keys())
    raise RuntimeError("Graphiti MCP exposes neither add_memory nor add_episode")


def choose_delete_tool(tools: list[Any]) -> tuple[str, set[str]]:
    for tool in tools:
        if tool.name == "delete_episode":
            schema = tool.inputSchema or {}
            return tool.name, set((schema.get("properties") or {}).keys())
    raise RuntimeError("Graphiti MCP does not expose delete_episode")


def choose_episode_list_tool(tools: list[Any]) -> tuple[str, set[str]]:
    for tool in tools:
        if tool.name == "get_episodes":
            schema = tool.inputSchema or {}
            return tool.name, set((schema.get("properties") or {}).keys())
    raise RuntimeError("Graphiti MCP does not expose get_episodes")


def build_tool_arguments(row: dict[str, Any], accepted: set[str]) -> dict[str, Any]:
    previous = row.get("previous_graphiti_episode_id")
    candidates: dict[str, Any] = {
        "name": f"entry:{row['entry_id']} {row['entry_type']}: {row['participant_name']}",
        "episode_body": build_episode_body(row),
        "group_id": row["group_id"],
        "source": "json",
        "source_description": (
            "Canonical Gbuzz PostgreSQL session entry; MinIO object pointers are in provenance"
        ),
        "reference_time": row["occurred_at"].isoformat(),
        "saga": str(row["session_id"]),
        "custom_extraction_instructions": (
            "Treat the chat session as the knowledge container. Treat document participants as "
            "speakers contributing evidence. Extract entities, relationships, decisions, requirements, "
            "procedures and time-qualified facts. Preserve the supplied PostgreSQL and MinIO provenance."
        ),
    }
    return {
        key: value for key, value in candidates.items()
        if key in accepted and value is not None
    }


def tool_result_object(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        nested = structured.get("result")
        return nested if isinstance(nested, dict) else structured
    for item in getattr(result, "content", []):
        value = getattr(item, "text", None)
        if not value:
            continue
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return {}


async def reconcile_episodes(
    pool: asyncpg.Pool,
    session: ClientSession,
    tool_name: str,
    accepted: set[str],
) -> int:
    groups = await pool.fetch(
        """SELECT group_id,min(coalesce(submitted_at,updated_at)) AS first_seen
           FROM gcor.graphiti_projection
           WHERE operation='add' AND status IN ('submitted','failed','processing')
             AND reconciled_at IS NULL
           GROUP BY group_id
           ORDER BY first_seen
           LIMIT 100"""
    )
    if not groups:
        return 0
    candidates = {
        "group_ids": [group["group_id"] for group in groups],
        "max_episodes": 1000,
    }
    result = await session.call_tool(
        tool_name, {key: value for key, value in candidates.items() if key in accepted}
    )
    if result.isError:
        return 0
    reconciled = 0
    for episode in tool_result_object(result).get("episodes", []):
        name = str(episode.get("name") or "")
        if not name.startswith("entry:"):
            continue
        entry_token = name.split(" ", 1)[0].removeprefix("entry:")
        try:
            entry_id = __import__("uuid").UUID(entry_token)
            episode_id = __import__("uuid").UUID(str(episode["uuid"]))
        except (ValueError, KeyError):
            continue
        result_tag = await pool.execute(
            """UPDATE gcor.graphiti_projection gp
               SET graphiti_episode_id=$2,reconciled_at=now(),
                   operation=CASE WHEN d.metadata->>'knowledge_state' IN ('archived','rejected')
                                  THEN 'delete' ELSE gp.operation END,
                   status=CASE WHEN d.metadata->>'knowledge_state' IN ('archived','rejected')
                               THEN 'pending' ELSE 'submitted' END,
                   updated_at=now()
               FROM gcor.knowledge_entries e
               LEFT JOIN gcor.documents d ON d.id=e.source_document_id
               WHERE gp.entry_id=$1 AND e.id=gp.entry_id
                 AND gp.status IN ('submitted','failed','processing')
                 AND gp.reconciled_at IS NULL""",
            entry_id, episode_id,
        )
        reconciled += int(result_tag.endswith("1"))
    return reconciled


async def claim_entry(pool: asyncpg.Pool) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        WITH candidate AS (
            SELECT entry_id
            FROM gcor.graphiti_projection
            WHERE attempts < $1
              AND NOT EXISTS (
                SELECT 1 FROM gcor.graphiti_projection inflight
                WHERE inflight.operation='add' AND inflight.status='submitted'
                  AND inflight.reconciled_at IS NULL
              )
              AND (
                (status IN ('pending','failed') AND next_attempt_at <= now())
                OR (status='processing' AND updated_at < now()-($2::int * interval '1 minute'))
              )
            ORDER BY next_attempt_at,created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        ), claimed AS (
            UPDATE gcor.graphiti_projection p
            SET status='processing',attempts=p.attempts+1,error=NULL,updated_at=now()
            FROM candidate c
            WHERE p.entry_id=c.entry_id
            RETURNING p.entry_id,p.graphiti_episode_id,p.group_id,p.previous_episode_id,p.attempts
        )
        SELECT v.*,c.graphiti_episode_id,c.group_id,c.attempts,
               previous.graphiti_episode_id AS previous_graphiti_episode_id
        FROM claimed c
        JOIN gcor.session_knowledge v ON v.entry_id=c.entry_id
        LEFT JOIN gcor.graphiti_projection previous ON previous.entry_id=c.previous_episode_id
        """,
        MAX_ATTEMPTS, PROCESSING_TIMEOUT_MINUTES,
    )


async def expire_stale_submissions(pool: asyncpg.Pool) -> int:
    result = await pool.execute(
        """UPDATE gcor.graphiti_projection
           SET status='failed',
               error='Graphiti accepted the episode but it did not become queryable before the submission timeout',
               next_attempt_at=now()+(LEAST(300,POWER(2,LEAST(attempts,8)))::int * interval '1 second'),
               updated_at=now()
           WHERE operation='add' AND status='submitted' AND reconciled_at IS NULL
             AND submitted_at < now()-($1::int * interval '1 minute')""",
        SUBMISSION_TIMEOUT_MINUTES,
    )
    return int(result.rsplit(" ", 1)[-1])


async def mark_submitted(
    pool: asyncpg.Pool, entry_id: Any, operation: str
) -> None:
    await pool.execute(
        """UPDATE gcor.graphiti_projection
           SET status='submitted',submitted_at=now(),error=NULL,
               reconciled_at=CASE WHEN $2='delete' THEN now() ELSE reconciled_at END,
               updated_at=now()
           WHERE entry_id=$1""",
        entry_id, operation,
    )


async def mark_failed(pool: asyncpg.Pool, entry_id: Any, attempts: int, error: Exception) -> None:
    delay_seconds = min(300, 2 ** min(attempts, 8))
    await pool.execute(
        """UPDATE gcor.graphiti_projection
           SET status='failed',error=$2,
               next_attempt_at=now()+($3::int * interval '1 second'),updated_at=now()
           WHERE entry_id=$1""",
        entry_id, str(error)[:2000], delay_seconds,
    )


async def project_forever(pool: asyncpg.Pool) -> None:
    async with (
        httpx.AsyncClient(
            headers={"Host": GRAPHITI_MCP_HOST_HEADER}, follow_redirects=True
        ) as http_client,
        streamable_http_client(
            GRAPHITI_MCP_URL, http_client=http_client
        ) as (read_stream, write_stream, _),
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            tool_name, accepted = choose_ingest_tool(tools)
            delete_tool_name, delete_accepted = choose_delete_tool(tools)
            list_tool_name, list_accepted = choose_episode_list_tool(tools)
            while True:
                __import__('pathlib').Path('/tmp/projector-heartbeat').touch()
                await reconcile_episodes(
                    pool, session, list_tool_name, list_accepted
                )
                await expire_stale_submissions(pool)
                row = await claim_entry(pool)
                if row is None:
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                try:
                    if row["graphiti_operation"] == "delete":
                        delete_candidates = {
                            "uuid": str(row["graphiti_episode_id"]),
                            "episode_uuid": str(row["graphiti_episode_id"]),
                            "group_id": row["group_id"],
                        }
                        result = await session.call_tool(
                            delete_tool_name,
                            {key: value for key, value in delete_candidates.items() if key in delete_accepted},
                        )
                    else:
                        result = await session.call_tool(
                            tool_name,
                            build_tool_arguments(dict(row), accepted),
                        )
                    if result.isError:
                        rendered = " ".join(
                            getattr(item, "text", str(item)) for item in result.content
                        )
                        raise RuntimeError(rendered or "Graphiti MCP rejected the episode")
                    await mark_submitted(
                        pool, row["entry_id"], row["graphiti_operation"]
                    )
                except Exception as error:
                    await mark_failed(pool, row["entry_id"], row["attempts"], error)


async def run() -> None:
    pool = await asyncpg.create_pool(**POSTGRES, min_size=1, max_size=3)
    try:
        while True:
            try:
                await project_forever(pool)
            except Exception as error:
                print(f"Graphiti MCP connection failed: {render_error(error)}", flush=True)
                await asyncio.sleep(min(30, max(2, POLL_SECONDS)))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
