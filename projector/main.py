import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx


POSTGRES = {
    "host": os.getenv("POSTGRES_HOST", "postgres"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
    "database": os.environ["POSTGRES_DB"],
}
PROXY_URL = os.getenv("GCOR_PROXY_URL", "http://gcor-proxy:5001").rstrip("/")
RELAY_URL = os.getenv("BUZZ_RELAY_HTTP_URL", "http://relay:3000").rstrip("/")
SECRET = os.getenv("STACK_API_SECRET", "") or os.environ["INGEST_WEBHOOK_SECRET"]
WORKLOAD_TOKEN = os.getenv("PROJECTOR_WORKLOAD_TOKEN", "")
POLL_SECONDS = float(os.getenv("PROJECTOR_POLL_SECONDS", "2"))
BATCH_SIZE = int(os.getenv("PROJECTOR_BATCH_SIZE", "50"))
KINDS = [int(value) for value in os.getenv("PROJECTOR_EVENT_KINDS", "9,40002,45001,45003").split(",") if value.strip()]
KNOWLEDGE_MODE = os.getenv("PROJECTOR_KNOWLEDGE_MODE", "selective").strip().casefold()

LOW_VALUE_MESSAGES = {
    "ack", "acknowledged", "cool", "done", "got it", "great", "hello", "hey", "hi",
    "ok", "okay", "thanks", "thank you", "understood", "yes", "no",
}
KNOWLEDGE_SIGNALS = re.compile(
    r"\b(action|approved?|blocker|decision|finding|incident|issue|lesson|mitigation|"
    r"owner|policy|recommendation|requirement|resolution|risk|root cause|status|"
    r"tenant health|todo|next step)\b",
    re.IGNORECASE,
)


def knowledge_disposition(content: str) -> tuple[bool, str]:
    """Return whether a channel message belongs in semantic retrieval.

    All messages are still archived. This decision only controls promotion into
    the retrieval index.
    """
    normalized = " ".join(content.split()).strip()
    comparable = re.sub(r"^@[\w.-]+\s*", "", normalized).strip(" .,!?:;").casefold()
    if KNOWLEDGE_MODE == "all":
        return True, "mode_all"
    if KNOWLEDGE_MODE == "archive_only":
        return False, "mode_archive_only"
    if not comparable:
        return False, "empty_or_mention_only"
    if comparable in LOW_VALUE_MESSAGES:
        return False, "conversational_chatter"
    if KNOWLEDGE_SIGNALS.search(comparable):
        return True, "knowledge_signal"
    if len(comparable) >= 80 or len(comparable.split()) >= 14:
        return True, "substantive_message"
    return False, "short_without_knowledge_signal"


def parse_attachments(tags: Any) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    if not isinstance(tags, list):
        return attachments
    for tag in tags:
        if not isinstance(tag, list) or not tag or tag[0] != "imeta":
            continue
        item: dict[str, str] = {}
        for value in tag[1:]:
            if not isinstance(value, str) or " " not in value:
                continue
            key, field_value = value.split(" ", 1)
            if key == "url":
                item["url"] = field_value
            elif key == "m":
                item["media_type"] = field_value
            elif key == "filename":
                item["file_name"] = field_value
        if item.get("url"):
            attachments.append(item)
    return attachments


def internal_media_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    if not parsed.path.startswith("/media/"):
        raise ValueError("Buzz attachment URL is not a relay media path")
    return f"{RELAY_URL}{parsed.path}"


async def post_ingest(client: httpx.AsyncClient, data: dict[str, str], files: Any = None) -> dict[str, Any]:
    headers = {"X-Gcor-Webhook-Secret": SECRET}
    if WORKLOAD_TOKEN:
        headers.update({"X-Gcor-Workload-Authorization": f"Bearer {WORKLOAD_TOKEN}", "X-Gcor-Channel-Id": data["channel_id"]})
    response = await client.post(
        f"{PROXY_URL}/api/ingest",
        data=data,
        files=files,
        headers=headers,
    )
    response.raise_for_status()
    return response.json()


async def process_event(pool: asyncpg.Pool, client: httpx.AsyncClient, row: asyncpg.Record) -> None:
    event_id = row["event_id"]
    await pool.execute(
        """INSERT INTO gcor.event_projection
           (event_id,event_kind,channel_id,status,attempts,event_created_at)
           VALUES ($1,$2,$3,'processing',1,$4)
           ON CONFLICT (event_id) DO UPDATE SET status='processing',attempts=gcor.event_projection.attempts+1,
               error=NULL,updated_at=now()""",
        event_id, row["kind"], row["channel_id"], row["created_at"],
    )
    access_level = "public" if str(row["visibility"] or "").casefold() == "public" else "private"
    common = {
        "channel_name": row["channel_name"] or row["channel_id"],
        "channel_id": row["channel_id"],
        "event_id": event_id,
        "event_kind": f"buzz.kind.{row['kind']}",
        "event_timestamp": row["created_at"].isoformat(),
        "author_pubkey": row["author_pubkey"],
        "access_level": access_level,
    }
    last_result: dict[str, Any] | None = None
    content = (row["content"] or "").strip()
    attachments = parse_attachments(row["tags"])
    if content:
        promote, reason = knowledge_disposition(content)
        last_result = await post_ingest(client, common | {
            "text": content,
            "title": f"{row['channel_name'] or row['channel_id']} message",
            "source_uri": f"buzz://event/{event_id}",
            "archive_only": "false" if promote else "true",
            "metadata_json": json.dumps({
                "record_type": "buzz_event",
                "projected": True,
                "buzz_event_kind": row["kind"],
                "knowledge_disposition": "indexed" if promote else "archived",
                "knowledge_reason": reason,
            }),
        })
    for index, attachment in enumerate(attachments):
        media_response = await client.get(internal_media_url(attachment["url"]))
        media_response.raise_for_status()
        file_name = attachment.get("file_name") or attachment["url"].rsplit("/", 1)[-1]
        media_type = attachment.get("media_type") or media_response.headers.get("content-type", "application/octet-stream")
        last_result = await post_ingest(
            client,
            common | {
                "title": file_name,
                "source_uri": f"buzz://event/{event_id}#attachment:{index + 1}",
                "metadata_json": json.dumps({"record_type": "buzz_attachment", "projected": True, "parent_event_id": event_id}),
            },
            files={"file": (file_name, media_response.content, media_type)},
        )
    status = "indexed" if last_result and last_result.get("status", "indexed") == "indexed" else "skipped"
    document_id = last_result.get("document_id") if last_result else None
    record_id = last_result.get("record_id") if last_result else None
    await pool.execute(
        """UPDATE gcor.event_projection SET status=$2,document_id=$3::uuid,record_id=$4::uuid,
                  error=NULL,updated_at=now() WHERE event_id=$1""",
        event_id, status, document_id, record_id,
    )


async def run() -> None:
    pool = await asyncpg.create_pool(**POSTGRES, min_size=1, max_size=3)
    try:
        timeout = httpx.Timeout(90, connect=10)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            while True:
                __import__('pathlib').Path('/tmp/projector-heartbeat').touch()
                rows = await pool.fetch(
                    """SELECT encode(e.id,'hex') AS event_id,e.kind,e.created_at,e.channel_id::text,
                              c.name AS channel_name,c.visibility::text AS visibility,encode(e.pubkey,'hex') AS author_pubkey,
                              e.content,e.tags
                       FROM events e JOIN channels c ON c.id=e.channel_id
                       LEFT JOIN gcor.event_projection p ON p.event_id=encode(e.id,'hex')
                       WHERE e.deleted_at IS NULL AND e.channel_id IS NOT NULL AND e.kind=ANY($1::int[])
                         AND (p.event_id IS NULL OR (p.status='failed' AND p.updated_at < now()-interval '10 seconds'))
                       ORDER BY e.created_at,e.id LIMIT $2""",
                    KINDS, BATCH_SIZE,
                )
                for row in rows:
                    __import__('pathlib').Path('/tmp/projector-heartbeat').touch()
                    try:
                        await process_event(pool, client, row)
                    except Exception as error:
                        await pool.execute(
                            """UPDATE gcor.event_projection SET status='failed',error=$2,updated_at=now() WHERE event_id=$1""",
                            row["event_id"], str(error)[:2000],
                        )
                await asyncio.sleep(POLL_SECONDS)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
