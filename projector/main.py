import asyncio
import hashlib
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
MAX_ATTACHMENT_BYTES = int(os.getenv("PROJECTOR_MAX_ATTACHMENT_BYTES", str(50 * 1024 * 1024)))
ATTACHMENT_FETCH_ATTEMPTS = int(os.getenv("PROJECTOR_ATTACHMENT_FETCH_ATTEMPTS", "3"))
EXTRACTION_VERSION = os.getenv("PROJECTOR_EXTRACTION_VERSION", "gcor.parser.v1")
HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")
MEDIA_SUFFIXES = {
    "application/pdf": {".pdf"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {".pptx"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "text/csv": {".csv"},
}

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
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            return attachments
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
            elif key == "x":
                item["sha256"] = field_value.casefold()
            elif key == "size":
                item["size"] = field_value
        if item.get("url"):
            attachments.append(item)
    return attachments


def internal_media_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    if not parsed.path.startswith("/media/"):
        raise ValueError("Buzz attachment URL is not a relay media path")
    return f"{RELAY_URL}{parsed.path}"


def validate_attachment_type(attachment: dict[str, str]) -> None:
    media_type = attachment.get("media_type", "").split(";", 1)[0].casefold()
    file_name = attachment.get("file_name", "").casefold()
    allowed = MEDIA_SUFFIXES.get(media_type)
    if allowed and file_name and not any(file_name.endswith(suffix) for suffix in allowed):
        raise ValueError("Buzz attachment MIME type and filename extension disagree")


def validate_content_type(content: bytes, media_type: str) -> None:
    media_type = media_type.split(";", 1)[0].casefold()
    if media_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("Buzz attachment content does not match PDF media type")
    if media_type.startswith("image/"):
        signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
        if not content.startswith(signatures):
            raise ValueError("Buzz attachment content does not match image media type")
    if "openxmlformats-officedocument" in media_type and not content.startswith(b"PK"):
        raise ValueError("Buzz attachment content does not match Office media type")


async def fetch_attachment(client: httpx.AsyncClient, attachment: dict[str, str]) -> tuple[bytes, str]:
    validate_attachment_type(attachment)
    expected_size: int | None = None
    if attachment.get("size"):
        try:
            expected_size = int(attachment["size"])
        except ValueError as error:
            raise ValueError("Buzz attachment size is invalid") from error
        if expected_size < 0 or expected_size > MAX_ATTACHMENT_BYTES:
            raise ValueError("Buzz attachment exceeds the projector byte limit")
    expected_sha256 = attachment.get("sha256", "")
    if expected_size is None or not expected_sha256:
        raise ValueError("Buzz attachment signed size and SHA-256 are required")
    if not HEX_64.fullmatch(expected_sha256):
        raise ValueError("Buzz attachment SHA-256 is invalid")
    media_key = urlparse(attachment["url"]).path.rsplit("/", 1)[-1].split(".", 1)[0].casefold()
    if media_key != expected_sha256:
        raise ValueError("Buzz media key does not match its signed SHA-256")
    digest = hashlib.sha256()
    content = bytearray()
    async with client.stream("GET", internal_media_url(attachment["url"])) as response:
        response.raise_for_status()
        header_size = response.headers.get("content-length")
        if header_size:
            try:
                declared_size = int(header_size)
            except ValueError as error:
                raise ValueError("Buzz attachment Content-Length is invalid") from error
            if declared_size > MAX_ATTACHMENT_BYTES:
                raise ValueError("Buzz attachment exceeds the projector byte limit")
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > MAX_ATTACHMENT_BYTES:
                raise ValueError("Buzz attachment exceeds the projector byte limit")
            content.extend(chunk)
            digest.update(chunk)
    if expected_size is not None and len(content) != expected_size:
        raise ValueError("Buzz attachment size does not match its signed metadata")
    actual_sha256 = digest.hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ValueError("Buzz attachment SHA-256 does not match its signed metadata")
    validate_content_type(bytes(content), attachment.get("media_type", ""))
    return bytes(content), actual_sha256


async def fetch_attachment_with_retries(client: httpx.AsyncClient, attachment: dict[str, str]) -> tuple[bytes, str]:
    """Retry transient relay failures; integrity and policy failures are final."""
    for attempt in range(1, ATTACHMENT_FETCH_ATTEMPTS + 1):
        try:
            return await fetch_attachment(client, attachment)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            if attempt == ATTACHMENT_FETCH_ATTEMPTS:
                raise
            await asyncio.sleep(min(2 ** (attempt - 1), 5))
    raise RuntimeError("attachment fetch retry loop exhausted")


async def post_ingest(client: httpx.AsyncClient, data: dict[str, str], files: Any = None) -> dict[str, Any]:
    headers = {"X-Gcor-Webhook-Secret": SECRET}
    if WORKLOAD_TOKEN:
        headers.update({"X-Gcor-Workload-Authorization": f"Bearer {WORKLOAD_TOKEN}", "X-Gcor-Channel-Id": data["channel_id"],
                        "X-Gcor-Access-Level": data["access_level"]})
    response = await client.post(
        f"{PROXY_URL}/api/ingest",
        data=data,
        files=files,
        headers=headers,
    )
    response.raise_for_status()
    return response.json()


async def _process_event(pool: Any, client: httpx.AsyncClient, row: asyncpg.Record) -> None:
    event_id = row["event_id"]
    projection_attempt = await pool.fetchval(
        """INSERT INTO gcor.event_projection
           (event_id,event_kind,channel_id,status,attempts,event_created_at)
           VALUES ($1,$2,$3,'processing',1,$4)
           ON CONFLICT (event_id) DO UPDATE SET status='processing',attempts=gcor.event_projection.attempts+1,
               error=NULL,updated_at=now()
           RETURNING attempts""",
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
        attachment_content, attachment_sha256 = await fetch_attachment_with_retries(client, attachment)
        file_name = attachment.get("file_name") or attachment["url"].rsplit("/", 1)[-1]
        media_type = attachment.get("media_type") or "application/octet-stream"
        last_result = await post_ingest(
            client,
            common | {
                "title": file_name,
                "source_uri": f"buzz://event/{event_id}#attachment:{index + 1}",
                "metadata_json": json.dumps({
                    "record_type": "buzz_attachment",
                    "projected": True,
                    "parent_event_id": event_id,
                    "attachment_index": index + 1,
                    "attachment_sha256": attachment_sha256,
                    "attachment_size": len(attachment_content),
                    "projection_attempt": projection_attempt,
                    "extraction_version": EXTRACTION_VERSION,
                }),
            },
            files={"file": (file_name, attachment_content, media_type)},
        )
    status = "indexed" if last_result and last_result.get("status", "indexed") == "indexed" else "skipped"
    document_id = last_result.get("document_id") if last_result else None
    record_id = last_result.get("record_id") if last_result else None
    await pool.execute(
        """UPDATE gcor.event_projection SET status=$2,document_id=$3::uuid,record_id=$4::uuid,
                  extraction_version=$5,error=NULL,updated_at=now() WHERE event_id=$1""",
        event_id, status, document_id, record_id, EXTRACTION_VERSION,
    )


async def process_event(pool: asyncpg.Pool, client: httpx.AsyncClient, row: asyncpg.Record) -> None:
    """Serialize one signed event across projector replicas and ambiguous retries."""
    event_id = row["event_id"]
    async with pool.acquire() as connection:
        locked = await connection.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", event_id,
        )
        if not locked:
            return
        try:
            await _process_event(connection, client, row)
        finally:
            await connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))", event_id,
            )


async def invalidate_stale_evidence(pool: asyncpg.Pool) -> int:
    """Atomically remove rebuildable derivatives while retaining immutable evidence."""
    result = await pool.execute(
        """WITH stale_docs AS MATERIALIZED (
               SELECT d.id FROM gcor.documents d
               LEFT JOIN public.events e ON encode(e.id,'hex')=d.metadata->>'parent_event_id'
               LEFT JOIN public.channels c ON c.id::text=d.metadata->>'channel_id'
               WHERE d.metadata->>'record_type'='buzz_attachment'
                 AND (e.deleted_at IS NOT NULL OR c.deleted_at IS NOT NULL OR c.archived_at IS NOT NULL
                      OR d.metadata->>'knowledge_state' IN ('archived','rejected','superseded')
                      OR EXISTS (SELECT 1 FROM gcor.documents newer
                           WHERE newer.source_uri=d.source_uri AND newer.id<>d.id
                             AND newer.metadata->>'extraction_version' IS DISTINCT FROM d.metadata->>'extraction_version'
                             AND newer.updated_at>d.updated_at))
           ), stale_nodes AS MATERIALIZED (
               SELECT id FROM gcor.nodes WHERE document_id IN (SELECT id FROM stale_docs)
           ), removed_edges AS (
               DELETE FROM gcor.edges WHERE source_id IN (SELECT id FROM stale_nodes)
                  OR target_id IN (SELECT id FROM stale_nodes) RETURNING id
           ), removed_chunks AS (
               DELETE FROM gcor.chunks WHERE document_id IN (SELECT id FROM stale_docs) RETURNING id
           ), removed_nodes AS (
               DELETE FROM gcor.nodes WHERE id IN (SELECT id FROM stale_nodes) RETURNING id
           )
           UPDATE gcor.documents SET metadata=metadata || jsonb_build_object(
               'derivatives_invalidated',true,'derivatives_invalidated_at',now())
           WHERE id IN (SELECT id FROM stale_docs)""")
    return int(result.rsplit(" ", 1)[-1])


async def run() -> None:
    pool = await asyncpg.create_pool(**POSTGRES, min_size=1, max_size=3)
    try:
        timeout = httpx.Timeout(90, connect=10)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            while True:
                __import__('pathlib').Path('/tmp/projector-heartbeat').touch()
                await invalidate_stale_evidence(pool)
                rows = await pool.fetch(
                    """SELECT encode(e.id,'hex') AS event_id,e.kind,e.created_at,e.channel_id::text,
                              c.name AS channel_name,c.visibility::text AS visibility,encode(e.pubkey,'hex') AS author_pubkey,
                              e.content,e.tags
                       FROM events e JOIN channels c ON c.id=e.channel_id
                       LEFT JOIN gcor.event_projection p ON p.event_id=encode(e.id,'hex')
                       WHERE e.deleted_at IS NULL AND e.channel_id IS NOT NULL AND e.kind=ANY($1::int[])
                         AND (p.event_id IS NULL OR p.extraction_version IS DISTINCT FROM $3
                              OR (p.status='failed' AND p.updated_at < now()-interval '10 seconds'))
                       ORDER BY e.created_at,e.id LIMIT $2""",
                    KINDS, BATCH_SIZE, EXTRACTION_VERSION,
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
