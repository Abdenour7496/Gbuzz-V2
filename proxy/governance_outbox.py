"""Durable audit publication; PostgreSQL is authoritative while storage is unavailable."""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def json_object(value):
    return json.loads(value) if isinstance(value, str) else value


def build_event(document_id, action, metadata, actor, note, default_bucket):
    event_id = uuid4()
    occurred_at = datetime.now(timezone.utc)
    bucket = str(metadata.get("bucket") or default_bucket)
    key = f"governance/{document_id}/events/{occurred_at:%Y/%m/%d/%H%M%S}-{event_id}.json"
    payload = {"schema_version": "1.0.0", "event_id": str(event_id), "document_id": str(document_id),
               "action": action, "actor": actor, "note": note, "occurred_at": occurred_at.isoformat(),
               "knowledge_state": metadata.get("knowledge_state"), "metadata": metadata}
    return event_id, bucket, key, payload


def publish_artifact(s3, row):
    payload = json_object(row["payload"])
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    try:
        s3.put_object(Bucket=row["bucket"], Key=row["object_key"], Body=body,
                      ContentType="application/json", IfNoneMatch="*",
                      Metadata={"event_id": str(row["event_id"]), "document_id": str(row["document_id"]),
                                "action": payload["action"], "sha256": digest})
    except ClientError as error:
        if str(error.response.get("Error", {}).get("Code")) not in {"PreconditionFailed", "412"}:
            raise
        existing = s3.head_object(Bucket=row["bucket"], Key=row["object_key"])
        if existing.get("Metadata", {}).get("sha256") != digest:
            raise RuntimeError("Governance artifact key conflict") from error
        # A previous attempt published successfully but crashed before its DB acknowledgment.


async def publish_one(pool, s3):
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                """SELECT * FROM gcor.governance_outbox
                   WHERE published_at IS NULL AND next_attempt_at <= now()
                   ORDER BY created_at, event_id FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            if row is None:
                return False
            try:
                await asyncio.to_thread(publish_artifact, s3, row)
            except Exception as error:
                # Sanitized status; never persist URLs, credentials, or response bodies.
                code = type(error).__name__
                if isinstance(error, ClientError):
                    code += ":" + str(error.response.get("Error", {}).get("Code", "unknown"))[:80]
                await connection.execute(
                    """UPDATE gcor.governance_outbox SET attempts=attempts+1, last_error=$2,
                       next_attempt_at=now()+make_interval(secs => $3) WHERE event_id=$1""",
                    row["event_id"], code, min(300, 2 ** min(row["attempts"] + 1, 9)),
                )
            else:
                await connection.execute(
                    """UPDATE gcor.governance_outbox SET attempts=attempts+1, last_error=NULL,
                       published_at=now() WHERE event_id=$1""", row["event_id"],
                )
    return True


async def run_publisher(pool, s3):
    while True:
        try:
            worked = await publish_one(pool, s3)
        except Exception as error:
            logger.warning("Governance publisher retry after %s", type(error).__name__)
            worked = False
        if not worked:
            await asyncio.sleep(1)
