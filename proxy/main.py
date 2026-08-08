import hashlib
import ipaddress
import io
import json
import mimetypes
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import boto3
import httpx
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from pypdf import PdfReader

POSTGRES_DSN = os.getenv("POSTGRES_DSN")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ["POSTGRES_USER"]
POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]
POSTGRES_DB = os.environ["POSTGRES_DB"]
MINIO_ENDPOINT = os.environ["MINIO_ENDPOINT"]
MINIO_ACCESS_KEY = os.environ["MINIO_ROOT_USER"]
MINIO_SECRET_KEY = os.environ["MINIO_ROOT_PASSWORD"]
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "buzz-gcor")
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "openai")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
INGEST_WEBHOOK_SECRET = os.getenv("INGEST_WEBHOOK_SECRET", "")
STACK_API_SECRET = os.getenv("STACK_API_SECRET", "") or INGEST_WEBHOOK_SECRET
ENFORCE_STACK_API_SECRET = os.getenv("ENFORCE_STACK_API_SECRET", "true").strip().lower() in {"1", "true", "yes", "on"}
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "180"))
CONCEPT_LIMIT_PER_CHUNK = int(os.getenv("CONCEPT_LIMIT_PER_CHUNK", "8"))
MAX_INGEST_FILE_BYTES = int(os.getenv("MAX_INGEST_FILE_BYTES", str(25 * 1024 * 1024)))
ATTACHMENT_FETCH_TIMEOUT_SECONDS = float(os.getenv("ATTACHMENT_FETCH_TIMEOUT_SECONDS", "20"))
ATTACHMENT_FETCH_MAX_RETRIES = int(os.getenv("ATTACHMENT_FETCH_MAX_RETRIES", "2"))
ATTACHMENT_FETCH_RETRY_BACKOFF_SECONDS = float(os.getenv("ATTACHMENT_FETCH_RETRY_BACKOFF_SECONDS", "0.5"))
ATTACHMENT_FETCH_RETRY_BACKOFF_MAX_SECONDS = float(os.getenv("ATTACHMENT_FETCH_RETRY_BACKOFF_MAX_SECONDS", "8"))
MAX_ATTACHMENTS_PER_REQUEST = int(os.getenv("MAX_ATTACHMENTS_PER_REQUEST", "100"))
REMOTE_FETCH_ALLOWED_HOSTS = [host.strip().casefold() for host in os.getenv("REMOTE_FETCH_ALLOWED_HOSTS", "").split(",") if host.strip()]
REMOTE_FETCH_BLOCK_PRIVATE_HOSTS = os.getenv("REMOTE_FETCH_BLOCK_PRIVATE_HOSTS", "false").strip().lower() in {"1", "true", "yes", "on"}

ROOT_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = ROOT_DIR / "schemas"
CHAT_SESSION_SCHEMA_PATH = SCHEMAS_DIR / "chat-session.schema.json"
MARKDOWN_ENVELOPE_SCHEMA_PATH = SCHEMAS_DIR / "markdown-envelope.schema.json"

QUOTED_CONCEPT_PATTERN = re.compile(r'["“]([^"”]{2,80})["”]')
PROPER_NOUN_PHRASE_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*)+\b")
IGNORED_CONCEPTS = {"A", "An", "And", "But", "For", "From", "In", "It", "Of", "On", "The", "This", "That", "To", "We", "With"}

REQUESTS = Counter("gcor_rag_requests_total", "GCOR retrieval requests")
REQUEST_DURATION = Histogram("gcor_rag_duration_seconds", "GCOR retrieval duration")
INGESTS = Counter("gcor_ingest_total", "GCOR document ingestion attempts")
ATTACHMENT_REPLAY_ATTEMPTS = Counter("gcor_attachment_replay_attempts_total", "Attachment replay attempts")
ATTACHMENT_REPLAY_RETRIES = Counter("gcor_attachment_replay_retries_total", "Attachment replay retry attempts")
ATTACHMENT_REPLAY_FAILURES = Counter("gcor_attachment_replay_failures_total", "Attachment replay failures")
ATTACHMENT_REPLAY_TIMEOUTS = Counter("gcor_attachment_replay_timeouts_total", "Attachment replay timeouts")
ATTACHMENT_FETCH_DURATION = Histogram("gcor_attachment_fetch_duration_seconds", "Attachment fetch duration")
REMOTE_FETCH_BLOCKED = Counter("gcor_remote_fetch_blocked_total", "Remote URL fetch requests rejected by policy", ["reason"])


def load_schema(path: Path, schema_name: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as schema_file:
            return json.load(schema_file)
    except FileNotFoundError as error:
        raise RuntimeError(f"Missing required schema file: {schema_name} ({path})") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON in schema file: {schema_name} ({path})") from error


CHAT_SESSION_SCHEMA = load_schema(CHAT_SESSION_SCHEMA_PATH, "chat-session")
MARKDOWN_ENVELOPE_SCHEMA = load_schema(MARKDOWN_ENVELOPE_SCHEMA_PATH, "markdown-envelope")
CHAT_SESSION_VALIDATOR = Draft202012Validator(CHAT_SESSION_SCHEMA, format_checker=FormatChecker())
MARKDOWN_ENVELOPE_VALIDATOR = Draft202012Validator(MARKDOWN_ENVELOPE_SCHEMA, format_checker=FormatChecker())


def raise_schema_error(field_name: str, error: ValidationError) -> None:
    location = ".".join(str(part) for part in error.path)
    if location:
        raise HTTPException(422, f"{field_name}.{location}: {error.message}")
    raise HTTPException(422, f"{field_name}: {error.message}")


def validate_json_schema(data: dict[str, Any], validator: Draft202012Validator, field_name: str) -> None:
    errors = sorted(validator.iter_errors(data), key=lambda item: list(item.path))
    if errors:
        raise_schema_error(field_name, errors[0])


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    hops: int = Field(default=2, ge=0, le=5)
    access_level: str = "public"
    min_confidence: float = Field(default=0, ge=0, le=1)
    agent_id: str | None = None
    vector_weight: float = Field(default=0.75, ge=0, le=1)
    lexical_weight: float = Field(default=0.25, ge=0, le=1)


class AskRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=6, ge=1, le=25)
    hops: int = Field(default=1, ge=0, le=3)
    access_level: str = "public"
    min_confidence: float = Field(default=0, ge=0, le=1)
    agent_id: str | None = None
    vector_weight: float = Field(default=0.75, ge=0, le=1)
    lexical_weight: float = Field(default=0.25, ge=0, le=1)
    max_citations: int = Field(default=4, ge=1, le=10)
    channel_id: str | None = None
    channel_name: str | None = None
    approved_only: bool = True
    prefer_recent_approved: bool = True


class AskReplyRequest(AskRequest):
    source_event_id: str | None = None
    max_reply_chars: int = Field(default=1800, ge=200, le=8000)
    include_scores: bool = False


class PromoteRequest(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    access_level: str = "public"
    agent_id: str | None = None
    source_uri: str | None = None
    channel_name: str | None = None
    channel_id: str | None = None
    event_id: str | None = None
    event_kind: str | None = None
    event_timestamp: str | None = None
    author_pubkey: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CorrectRequest(BaseModel):
    correction_text: str = Field(min_length=1)
    target_document_id: str | None = None
    target_source_uri: str | None = None
    correction_title: str = "Knowledge correction"
    access_level: str = "public"
    agent_id: str | None = None
    source_uri: str | None = None
    channel_name: str | None = None
    channel_id: str | None = None
    event_id: str | None = None
    event_kind: str | None = None
    event_timestamp: str | None = None
    author_pubkey: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApproveKnowledgeRequest(BaseModel):
    target_document_id: str | None = None
    target_source_uri: str | None = None
    approved_by: str | None = None
    note: str | None = None


class TransitionKnowledgeRequest(BaseModel):
    target_document_id: str | None = None
    target_source_uri: str | None = None
    transition: Literal["proposed", "approved", "superseded", "archived", "rejected"]
    changed_by: str | None = None
    note: str | None = None
    superseded_by_document_id: str | None = None
    superseded_by_source_uri: str | None = None


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(value) for value in values) + "]"


def chunk_text(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    offset = 0
    while offset < len(normalized):
        end = min(len(normalized), offset + CHUNK_SIZE)
        if end < len(normalized):
            boundary = normalized.rfind(" ", offset, end)
            if boundary > offset:
                end = boundary
        chunks.append(normalized[offset:end].strip())
        if end == len(normalized):
            break
        offset = max(end - CHUNK_OVERLAP, offset + 1)
    return chunks


def extract_concepts(text: str) -> list[str]:
    candidates = QUOTED_CONCEPT_PATTERN.findall(text)
    candidates.extend(PROPER_NOUN_PHRASE_PATTERN.findall(text))
    concepts: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate).strip(" .,:;!?()[]{}")
        key = normalized.casefold()
        if not normalized or normalized in IGNORED_CONCEPTS or key in seen:
            continue
        seen.add(key)
        concepts.append(normalized)
        if len(concepts) == CONCEPT_LIMIT_PER_CHUNK:
            break
    return concepts


def extract_text(content: bytes, media_type: str) -> str:
    if media_type == "application/pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    return content.decode("utf-8", errors="replace")


def channel_bucket_name(channel_name: str) -> str:
    slug = re.sub(r"[^a-z0-9.-]+", "-", channel_name.casefold()).strip("-.")
    slug = re.sub(r"[-.]{2,}", "-", slug)
    if not slug:
        return MINIO_BUCKET
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", slug):
        slug = f"chan-{slug.replace('.', '-') }"
    if len(slug) < 3:
        slug = f"ch-{slug}"
    if len(slug) > 63:
        digest = hashlib.sha1(channel_name.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[:54]}-{digest}"
    return slug


def object_key_suffix(file_name: str | None, media_type: str) -> str:
    if file_name:
        basename = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        suffix = Path(basename).suffix
        if suffix and re.fullmatch(r"\.[A-Za-z0-9_-]{1,16}", suffix):
            return suffix.casefold()
    guessed = mimetypes.guess_extension(media_type, strict=False)
    if guessed == ".ksh":
        return ".txt"
    if guessed and re.fullmatch(r"\.[A-Za-z0-9_-]{1,16}", guessed):
        return guessed.casefold()
    return ""


def parse_metadata_json(raw: str | None) -> dict[str, Any]:
    if raw is None or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(422, "metadata_json must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise HTTPException(422, "metadata_json must be a JSON object")
    return parsed


def parse_json_object(raw: str, field_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(422, f"{field_name} must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise HTTPException(422, f"{field_name} must be a JSON object")
    return parsed


def parse_attachments_json(raw: str | None) -> list[dict[str, Any]]:
    if raw is None or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HTTPException(422, "attachments_json must be valid JSON") from error
    if not isinstance(parsed, list):
        raise HTTPException(422, "attachments_json must be a JSON array")
    attachments: list[dict[str, Any]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise HTTPException(422, f"attachments_json[{index}] must be a JSON object")
        url = item.get("url")
        if not isinstance(url, str) or not url:
            raise HTTPException(422, f"attachments_json[{index}].url is required")
        attachments.append(item)
    if len(attachments) > MAX_ATTACHMENTS_PER_REQUEST:
        raise HTTPException(422, f"attachments_json exceeds MAX_ATTACHMENTS_PER_REQUEST ({MAX_ATTACHMENTS_PER_REQUEST})")
    return attachments


def host_matches_allowed_rule(host: str, rule: str) -> bool:
    if rule.startswith("*."):
        suffix = rule[1:]
        return host.endswith(suffix)
    return host == rule


def is_disallowed_address(address: ipaddress._BaseAddress) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def resolve_host_addresses(host: str) -> list[ipaddress._BaseAddress]:
    try:
        literal = ipaddress.ip_address(host)
        return [literal]
    except ValueError:
        pass
    try:
        addr_info = await __import__("asyncio").to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise HTTPException(422, f"Unable to resolve remote host: {host}") from error
    resolved: list[ipaddress._BaseAddress] = []
    for _, _, _, _, sockaddr in addr_info:
        ip_text = sockaddr[0]
        try:
            resolved.append(ipaddress.ip_address(ip_text))
        except ValueError:
            continue
    return resolved


async def validate_remote_fetch_url(file_url: str) -> Any:
    parsed = urlparse(file_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(422, "file_url must use http or https")
    host = (parsed.hostname or "").casefold()
    if not host:
        raise HTTPException(422, "file_url must include a hostname")
    if REMOTE_FETCH_ALLOWED_HOSTS and not any(host_matches_allowed_rule(host, rule) for rule in REMOTE_FETCH_ALLOWED_HOSTS):
        REMOTE_FETCH_BLOCKED.labels(reason="host_not_allowed").inc()
        raise HTTPException(422, f"remote host is not allowed: {host}")
    if REMOTE_FETCH_BLOCK_PRIVATE_HOSTS:
        addresses = await resolve_host_addresses(host)
        if not addresses:
            REMOTE_FETCH_BLOCKED.labels(reason="host_unresolved").inc()
            raise HTTPException(422, f"Unable to resolve remote host: {host}")
        if any(is_disallowed_address(address) for address in addresses):
            REMOTE_FETCH_BLOCKED.labels(reason="private_or_local_address").inc()
            raise HTTPException(422, f"remote host resolves to a private or local address: {host}")
    return parsed


def validate_chat_session_record(record: dict[str, Any]) -> dict[str, Any]:
    validate_json_schema(record, CHAT_SESSION_VALIDATOR, "session_json")
    required_fields = ["session_id", "channel_name", "channel_id", "started_at", "messages"]
    missing = [field for field in required_fields if field not in record]
    if missing:
        raise HTTPException(422, f"session_json is missing required fields: {', '.join(missing)}")
    if not isinstance(record["session_id"], str) or not record["session_id"].strip():
        raise HTTPException(422, "session_json.session_id must be a non-empty string")
    if not isinstance(record["channel_name"], str) or not record["channel_name"].strip():
        raise HTTPException(422, "session_json.channel_name must be a non-empty string")
    if not isinstance(record["channel_id"], str) or not record["channel_id"].strip():
        raise HTTPException(422, "session_json.channel_id must be a non-empty string")
    if not isinstance(record["started_at"], str) or not record["started_at"].strip():
        raise HTTPException(422, "session_json.started_at must be a non-empty string")
    if not isinstance(record["messages"], list) or not record["messages"]:
        raise HTTPException(422, "session_json.messages must be a non-empty array")
    for index, message in enumerate(record["messages"]):
        if not isinstance(message, dict):
            raise HTTPException(422, f"session_json.messages[{index}] must be an object")
        for field in ["message_id", "author_pubkey", "created_at"]:
            if not isinstance(message.get(field), str) or not message[field].strip():
                raise HTTPException(422, f"session_json.messages[{index}].{field} must be a non-empty string")
        if "content" in message and not isinstance(message["content"], str):
            raise HTTPException(422, f"session_json.messages[{index}].content must be a string when provided")
        if "attachments" in message:
            if not isinstance(message["attachments"], list):
                raise HTTPException(422, f"session_json.messages[{index}].attachments must be an array")
            for attachment_index, attachment in enumerate(message["attachments"]):
                if not isinstance(attachment, dict):
                    raise HTTPException(422, f"session_json.messages[{index}].attachments[{attachment_index}] must be an object")
                if not isinstance(attachment.get("url"), str) or not attachment["url"]:
                    raise HTTPException(422, f"session_json.messages[{index}].attachments[{attachment_index}].url is required")
    return record


def markdown_envelope(
    title: str,
    source_uri: str | None,
    access_level: str,
    channel_name: str | None,
    channel_id: str | None,
    event_id: str | None,
    event_kind: str | None,
    event_timestamp: str | None,
    author_pubkey: str | None,
    metadata: dict[str, Any],
    body: str,
) -> str:
    front_matter: dict[str, Any] = {
        "title": title,
        "source_uri": source_uri,
        "access_level": access_level,
        "channel_name": channel_name,
        "channel_id": channel_id,
        "event_id": event_id,
        "event_kind": event_kind,
        "event_timestamp": event_timestamp,
        "author_pubkey": author_pubkey,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    front_matter.update({key: value for key, value in metadata.items() if value is not None})
    yaml_lines = ["---"]
    for key, value in front_matter.items():
        if isinstance(value, (dict, list)):
            yaml_lines.append(f"{key}: {json.dumps(value, ensure_ascii=True)}")
        else:
            escaped = str(value).replace("\n", " ").replace("\r", " ")
            yaml_lines.append(f"{key}: \"{escaped}\"")
    yaml_lines.append("---")
    return "\n".join(yaml_lines) + "\n\n" + body.strip() + "\n"


def chat_session_to_markdown(record: dict[str, Any]) -> str:
    lines = [
        f"# Session {record['session_id']}",
        "",
        f"Channel: {record['channel_name']} ({record['channel_id']})",
        f"Started: {record['started_at']}",
        "",
        "## Messages",
        "",
    ]
    for message in record["messages"]:
        lines.append(f"### {message['created_at']} {message['author_pubkey']}")
        lines.append("")
        content = message.get("content", "")
        if content:
            lines.append(content)
            lines.append("")
        for attachment in message.get("attachments", []):
            name = attachment.get("name") or attachment.get("file_name") or "attachment"
            lines.append(f"- attachment: {name}")
            lines.append(f"  - url: {attachment['url']}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def sample_chat_session() -> dict[str, Any]:
    return {
        "session_id": "session-001",
        "channel_name": "Architecture Review",
        "channel_id": "chan-42",
        "started_at": "2026-08-08T10:00:00Z",
        "messages": [
            {
                "message_id": "msg-1",
                "author_pubkey": "npub1exampleauthor",
                "created_at": "2026-08-08T10:01:00Z",
                "content": "Please review the design notes.",
                "attachments": [
                    {
                        "url": "https://example.local/design-notes.md",
                        "name": "design-notes.md",
                        "media_type": "text/markdown",
                    }
                ],
            }
        ],
    }


def sample_markdown_envelope() -> dict[str, Any]:
    return {
        "front_matter": {
            "title": "Session session-001",
            "source_uri": "buzz-session://session-001",
            "access_level": "public",
            "channel_name": "Architecture Review",
            "channel_id": "chan-42",
            "event_id": "session-001",
            "event_kind": "session.replay",
            "event_timestamp": "2026-08-08T10:00:00Z",
            "author_pubkey": "npub1exampleauthor",
            "generated_at": "2026-08-08T10:01:00Z",
            "record_type": "chat_session",
            "record_format": "json",
        },
        "body_markdown": "# Session session-001\n\nChannel: Architecture Review (chan-42)\nStarted: 2026-08-08T10:00:00Z\n\n## Messages\n\n### 2026-08-08T10:01:00Z npub1exampleauthor\n\nPlease review the design notes.\n\n- attachment: design-notes.md\n  - url: https://example.local/design-notes.md\n",
    }


async def fetch_remote_file(file_url: str) -> tuple[bytes, str, str | None]:
    parsed = await validate_remote_fetch_url(file_url)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(file_url)
    response.raise_for_status()
    content = response.content
    if len(content) > MAX_INGEST_FILE_BYTES:
        raise HTTPException(413, f"file_url payload exceeds MAX_INGEST_FILE_BYTES ({MAX_INGEST_FILE_BYTES})")
    media_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    filename = parsed.path.rsplit("/", 1)[-1] if parsed.path else None
    return content, media_type, filename


async def fetch_remote_file_with_retries(file_url: str) -> tuple[bytes, str, str | None, int]:
    parsed = await validate_remote_fetch_url(file_url)
    attempts = ATTACHMENT_FETCH_MAX_RETRIES + 1
    for attempt in range(1, attempts + 1):
        ATTACHMENT_REPLAY_ATTEMPTS.inc()
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=ATTACHMENT_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
                response = await client.get(file_url)
            response.raise_for_status()
            content = response.content
            if len(content) > MAX_INGEST_FILE_BYTES:
                raise HTTPException(413, f"file_url payload exceeds MAX_INGEST_FILE_BYTES ({MAX_INGEST_FILE_BYTES})")
            media_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            filename = parsed.path.rsplit("/", 1)[-1] if parsed.path else None
            return content, media_type, filename, attempt
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError) as error:
            if isinstance(error, httpx.TimeoutException):
                ATTACHMENT_REPLAY_TIMEOUTS.inc()
            if attempt >= attempts:
                ATTACHMENT_REPLAY_FAILURES.inc()
                raise
            ATTACHMENT_REPLAY_RETRIES.inc()
            backoff = min(ATTACHMENT_FETCH_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), ATTACHMENT_FETCH_RETRY_BACKOFF_MAX_SECONDS)
            await __import__("asyncio").sleep(backoff)
        finally:
            ATTACHMENT_FETCH_DURATION.observe(time.perf_counter() - started)


async def ensure_bucket_name(client: Any, bucket_name: str) -> None:
    try:
        await __import__("asyncio").to_thread(client.head_bucket, Bucket=bucket_name)
    except ClientError:
        await __import__("asyncio").to_thread(client.create_bucket, Bucket=bucket_name)


async def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=60) as client:
        if EMBEDDING_BACKEND == "openai":
            if not OPENAI_API_KEY:
                raise HTTPException(500, "OPENAI_API_KEY is required for the openai embedding backend")
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": EMBEDDING_MODEL, "input": texts},
            )
            response.raise_for_status()
            return [item["embedding"] for item in response.json()["data"]]
        if EMBEDDING_BACKEND == "ollama":
            vectors = []
            for text in texts:
                response = await client.post(f"{OLLAMA_HOST.rstrip('/')}/api/embed", json={"model": EMBEDDING_MODEL, "input": text})
                if response.status_code == 404:
                    response = await client.post(
                        f"{OLLAMA_HOST.rstrip('/')}/api/embeddings",
                        json={"model": EMBEDDING_MODEL, "prompt": text},
                    )
                if response.status_code == 404:
                    response = await client.post(
                        f"{OLLAMA_HOST.rstrip('/')}/v1/embeddings",
                        json={"model": EMBEDDING_MODEL, "input": text},
                    )
                response.raise_for_status()
                payload = response.json()
                if "embeddings" in payload:
                    vectors.append(payload["embeddings"][0])
                elif "embedding" in payload:
                    vectors.append(payload["embedding"])
                else:
                    vectors.append(payload["data"][0]["embedding"])
            return vectors
    raise HTTPException(500, "EMBEDDING_BACKEND must be openai or ollama")


def minio_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


async def ensure_bucket(app: FastAPI) -> None:
    await ensure_bucket_name(app.state.s3, MINIO_BUCKET)


async def get_or_create_concept(
    connection: asyncpg.Connection,
    label: str,
    access_level: str,
    agent_id: str | None,
) -> UUID:
    concept_id = await connection.fetchval(
        """
        INSERT INTO gcor.nodes (node_type, label, access_level, agent_id, properties)
        VALUES ('Concept', $1, $2, $3, jsonb_build_object('extractor', 'rule-based-v1'))
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        label, access_level, agent_id,
    )
    if concept_id is not None:
        return concept_id
    return await connection.fetchval(
        """
        SELECT id FROM gcor.nodes
        WHERE node_type = 'Concept'
          AND lower(label) = lower($1)
          AND access_level = $2
          AND COALESCE(agent_id, '') = COALESCE($3, '')
        """,
        label, access_level, agent_id,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool_options = {"min_size": 1, "max_size": 10}
    if POSTGRES_DSN:
        pool_options["dsn"] = POSTGRES_DSN
    else:
        pool_options.update({
            "host": POSTGRES_HOST,
            "port": POSTGRES_PORT,
            "user": POSTGRES_USER,
            "password": POSTGRES_PASSWORD,
            "database": POSTGRES_DB,
        })
    app.state.pool = await asyncpg.create_pool(**pool_options)
    app.state.s3 = minio_client()
    await ensure_bucket(app)
    yield
    await app.state.pool.close()


app = FastAPI(title="gcor-proxy", version="0.1.0", lifespan=lifespan)


def verify_webhook(secret: str | None) -> None:
    if INGEST_WEBHOOK_SECRET and secret not in {INGEST_WEBHOOK_SECRET, STACK_API_SECRET}:
        raise HTTPException(401, "Invalid ingest webhook secret")


def verify_stack_api_secret(secret: str | None) -> None:
    if not ENFORCE_STACK_API_SECRET:
        return
    if not STACK_API_SECRET:
        raise HTTPException(500, "STACK_API_SECRET is required when ENFORCE_STACK_API_SECRET is enabled")
    if secret != STACK_API_SECRET:
        raise HTTPException(401, "Invalid stack API secret")


async def ingest_payload(
    request: Request,
    *,
    content: bytes,
    media_type: str,
    title: str,
    access_level: str,
    agent_id: str | None,
    source_uri: str | None,
    channel_name: str | None,
    channel_id: str | None,
    event_id: str | None,
    event_kind: str | None,
    event_timestamp: str | None,
    author_pubkey: str | None,
    file_url: str | None,
    file_name: str | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if len(content) > MAX_INGEST_FILE_BYTES:
        raise HTTPException(413, f"payload exceeds MAX_INGEST_FILE_BYTES ({MAX_INGEST_FILE_BYTES})")

    content_text = extract_text(content, media_type)
    chunks = chunk_text(content_text)
    if not chunks:
        raise HTTPException(422, "The supplied content contains no extractable text")
    digest = hashlib.sha256(content).hexdigest()
    now = datetime.now(timezone.utc)
    bucket_name = channel_bucket_name(channel_name) if channel_name else MINIO_BUCKET
    object_key = f"gcor/{now:%Y/%m/%d/%H%M%S}-{digest[:12]}{object_key_suffix(file_name, media_type)}"

    vectors = await embed(chunks)
    if len(vectors) != len(chunks):
        raise HTTPException(502, "Embedding provider returned an unexpected number of vectors")
    object_metadata = {
        "ingested_at": now.isoformat(),
        "sha256": digest,
        "access_level": access_level,
    }
    if channel_name:
        object_metadata["channel_name"] = channel_name[:128]
    if channel_id:
        object_metadata["channel_id"] = channel_id[:128]
    if event_id:
        object_metadata["event_id"] = event_id[:128]
    if event_timestamp:
        object_metadata["event_timestamp"] = event_timestamp[:128]
    if source_uri:
        object_metadata["source_uri"] = source_uri[:256]
    if file_url:
        object_metadata["file_url"] = file_url[:256]

    await ensure_bucket_name(request.app.state.s3, bucket_name)
    await __import__("asyncio").to_thread(
        request.app.state.s3.put_object,
        Bucket=bucket_name,
        Key=object_key,
        Body=content,
        ContentType=media_type,
        Metadata=object_metadata,
    )

    metadata.update({
        "ingested_at": now.isoformat(),
        "bucket": bucket_name,
        "channel_name": channel_name,
        "channel_id": channel_id,
        "event_id": event_id,
        "event_kind": event_kind,
        "event_timestamp": event_timestamp,
        "author_pubkey": author_pubkey,
        "file_url": file_url,
        "file_name": file_name,
        "content_bytes": len(content),
        "chunk_count": len(chunks),
    })

    async with request.app.state.pool.acquire() as connection:
        async with connection.transaction():
            document_id = await connection.fetchval(
                """
                INSERT INTO gcor.documents (content_sha256, title, source_uri, object_key, media_type, access_level, agent_id, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (content_sha256) DO UPDATE SET
                    updated_at = now(),
                    title = EXCLUDED.title,
                    source_uri = EXCLUDED.source_uri,
                    object_key = EXCLUDED.object_key,
                    media_type = EXCLUDED.media_type,
                    access_level = EXCLUDED.access_level,
                    agent_id = EXCLUDED.agent_id,
                    metadata = gcor.documents.metadata || EXCLUDED.metadata
                RETURNING id
                """,
                digest, title, source_uri, object_key, media_type, access_level, agent_id, json.dumps(metadata),
            )
            existing = await connection.fetchval("SELECT count(*) FROM gcor.chunks WHERE document_id = $1", document_id)
            if existing:
                return {
                    "document_id": str(document_id),
                    "deduplicated": True,
                    "chunks": existing,
                    "bucket": bucket_name,
                    "object_key": object_key,
                }
            document_node_id = await connection.fetchval(
                """INSERT INTO gcor.nodes (document_id, node_type, label, content, access_level, agent_id)
                   VALUES ($1, 'Document', $2, $3, $4, $5) RETURNING id""",
                document_id, title, content_text[:1000], access_level, agent_id,
            )
            for ordinal, (content_chunk, embedding) in enumerate(zip(chunks, vectors)):
                node_id = await connection.fetchval(
                    """INSERT INTO gcor.nodes (document_id, node_type, label, content, access_level, agent_id)
                       VALUES ($1, 'Chunk', $2, $3, $4, $5) RETURNING id""",
                    document_id, f"{title} #{ordinal + 1}", content_chunk, access_level, agent_id,
                )
                await connection.execute(
                    """INSERT INTO gcor.chunks (document_id, node_id, ordinal, content, token_count, embedding)
                       VALUES ($1, $2, $3, $4, $5, $6::vector)""",
                    document_id, node_id, ordinal, content_chunk, len(content_chunk.split()), vector_literal(embedding),
                )
                await connection.execute(
                    "INSERT INTO gcor.edges (source_id, target_id, relation) VALUES ($1, $2, 'CONTAINS')",
                    document_node_id, node_id,
                )
                for concept_label in extract_concepts(content_chunk):
                    concept_id = await get_or_create_concept(connection, concept_label, access_level, agent_id)
                    await connection.execute(
                        """
                        INSERT INTO gcor.edges (source_id, target_id, relation)
                        VALUES ($1, $2, 'ABOUT')
                        ON CONFLICT DO NOTHING
                        """,
                        node_id, concept_id,
                    )
    return {
        "document_id": str(document_id),
        "deduplicated": False,
        "chunks": len(chunks),
        "bucket": bucket_name,
        "object_key": object_key,
    }


async def run_retrieval_query(
    request: Request,
    *,
    query: str,
    top_k: int,
    hops: int,
    access_level: str,
    min_confidence: float,
    agent_id: str | None,
    vector_weight: float,
    lexical_weight: float,
    channel_id: str | None = None,
    channel_name: str | None = None,
    approved_only: bool = False,
    prefer_recent_approved: bool = False,
) -> tuple[list[Any], list[Any]]:
    now = datetime.now(timezone.utc)
    query_vector = vector_literal((await embed([query]))[0])
    weight_total = vector_weight + lexical_weight
    if weight_total <= 0:
        raise HTTPException(422, "At least one retrieval weight must be greater than zero")
    vector_weight = vector_weight / weight_total
    lexical_weight = lexical_weight / weight_total
    async with request.app.state.pool.acquire() as connection:
        matches = await connection.fetch(
            """
            WITH search_terms AS (
                SELECT websearch_to_tsquery('english', $7) AS query
            ), semantic_candidates AS (
                SELECT c.id, c.node_id, c.document_id, c.ordinal, c.content, c.embedding, d.title, d.source_uri, d.created_at, d.metadata, c.search_vector
                FROM gcor.chunks c
                JOIN gcor.documents d ON d.id = c.document_id
                JOIN gcor.nodes n ON n.id = c.node_id
                WHERE d.access_level = $2
                  AND ($3::text IS NULL OR d.agent_id = $3)
                  AND ($3::text IS NULL OR n.agent_id = $3)
                                    AND ($10::text IS NULL OR d.metadata->>'channel_id' = $10)
                                    AND ($11::text IS NULL OR d.metadata->>'channel_name' = $11)
                                    AND ($12::bool IS FALSE OR COALESCE(d.metadata->>'knowledge_state', 'approved') = 'approved')
                  AND n.confidence >= $4
                  AND n.valid_from <= $5 AND (n.valid_to IS NULL OR n.valid_to >= $5)
                ORDER BY c.embedding <=> $1::vector
                LIMIT ($6 * 4)
            ), lexical_candidates AS (
                SELECT c.id, c.node_id, c.document_id, c.ordinal, c.content, c.embedding, d.title, d.source_uri, d.created_at, d.metadata, c.search_vector
                FROM gcor.chunks c
                JOIN gcor.documents d ON d.id = c.document_id
                JOIN gcor.nodes n ON n.id = c.node_id
                CROSS JOIN search_terms
                WHERE d.access_level = $2
                  AND ($3::text IS NULL OR d.agent_id = $3)
                  AND ($3::text IS NULL OR n.agent_id = $3)
                                    AND ($10::text IS NULL OR d.metadata->>'channel_id' = $10)
                                    AND ($11::text IS NULL OR d.metadata->>'channel_name' = $11)
                                    AND ($12::bool IS FALSE OR COALESCE(d.metadata->>'knowledge_state', 'approved') = 'approved')
                  AND n.confidence >= $4
                  AND n.valid_from <= $5 AND (n.valid_to IS NULL OR n.valid_to >= $5)
                  AND c.search_vector @@ search_terms.query
                ORDER BY ts_rank_cd(c.search_vector, search_terms.query, 32) DESC
                LIMIT ($6 * 4)
            ), candidates AS (
                SELECT * FROM semantic_candidates
                UNION
                SELECT * FROM lexical_candidates
            )
                             SELECT c.id, c.node_id, c.document_id, c.ordinal, c.content, c.title, c.source_uri, c.created_at, c.metadata,
                   1 - (c.embedding <=> $1::vector) AS vector_score,
                   ts_rank_cd(c.search_vector, search_terms.query, 32) AS lexical_score,
                                     ($8 * (1 - (c.embedding <=> $1::vector)) +
                                        $9 * ts_rank_cd(c.search_vector, search_terms.query, 32)) AS score,
                                     CASE
                                         WHEN $13::bool IS FALSE THEN 0.0
                                         ELSE
                                             (
                                                 CASE WHEN COALESCE(c.metadata->>'knowledge_state', 'approved') = 'approved' THEN 0.12 ELSE 0.0 END
                                                 + CASE
                                                         WHEN COALESCE(NULLIF(c.metadata->>'knowledge_approved_at', '')::timestamptz, c.created_at) >= ($5 - interval '7 days') THEN 0.05
                                                         WHEN COALESCE(NULLIF(c.metadata->>'knowledge_approved_at', '')::timestamptz, c.created_at) >= ($5 - interval '30 days') THEN 0.02
                                                         ELSE 0.0
                                                     END
                                             )
                                     END AS governance_boost,
                                     (($8 * (1 - (c.embedding <=> $1::vector)) +
                                        $9 * ts_rank_cd(c.search_vector, search_terms.query, 32)) +
                                        CASE
                                         WHEN $13::bool IS FALSE THEN 0.0
                                         ELSE
                                             (
                                                 CASE WHEN COALESCE(c.metadata->>'knowledge_state', 'approved') = 'approved' THEN 0.12 ELSE 0.0 END
                                                 + CASE
                                                         WHEN COALESCE(NULLIF(c.metadata->>'knowledge_approved_at', '')::timestamptz, c.created_at) >= ($5 - interval '7 days') THEN 0.05
                                                         WHEN COALESCE(NULLIF(c.metadata->>'knowledge_approved_at', '')::timestamptz, c.created_at) >= ($5 - interval '30 days') THEN 0.02
                                                         ELSE 0.0
                                                     END
                                             )
                                     END
                                     + CASE
                                             WHEN $13::bool IS FALSE THEN 0.0
                                             ELSE EXTRACT(EPOCH FROM COALESCE(NULLIF(c.metadata->>'knowledge_approved_at', '')::timestamptz, c.created_at)) * 1e-12
                                         END
                                     ) AS ranked_score
                                             ,COALESCE(NULLIF(c.metadata->>'knowledge_approved_at', '')::timestamptz, c.created_at) AS governance_time
            FROM candidates c
            CROSS JOIN search_terms
                                         ORDER BY ranked_score DESC, governance_time DESC, c.ordinal
            LIMIT $6
            """,
            query_vector, access_level, agent_id, min_confidence, now, top_k,
                        query, vector_weight, lexical_weight, channel_id, channel_name, approved_only, prefer_recent_approved,
        )
        seed_ids = [row["node_id"] for row in matches]
        graph_nodes = []
        if seed_ids and hops:
            graph_nodes = await connection.fetch(
                """
                WITH RECURSIVE walk(node_id, depth, path) AS (
                    SELECT seed_id, 0, ARRAY[seed_id]
                    FROM unnest($1::uuid[]) AS seed_id
                    UNION ALL
                    SELECT CASE WHEN e.source_id = w.node_id THEN e.target_id ELSE e.source_id END,
                           w.depth + 1,
                           w.path || CASE WHEN e.source_id = w.node_id THEN e.target_id ELSE e.source_id END
                    FROM walk w JOIN gcor.edges e ON e.source_id = w.node_id OR e.target_id = w.node_id
                    WHERE w.depth < $2
                      AND NOT (CASE WHEN e.source_id = w.node_id THEN e.target_id ELSE e.source_id END = ANY(w.path))
                )
                SELECT DISTINCT n.id, n.node_type, n.label, n.content, n.confidence, min(w.depth) AS depth
                FROM walk w JOIN gcor.nodes n ON n.id = w.node_id
                WHERE n.access_level = $3 AND n.confidence >= $4
                  AND n.valid_from <= $5 AND (n.valid_to IS NULL OR n.valid_to >= $5)
                  AND ($6::text IS NULL OR n.agent_id = $6)
                GROUP BY n.id, n.node_type, n.label, n.content, n.confidence
                ORDER BY depth, n.confidence DESC
                """,
                seed_ids, hops, access_level, min_confidence, now, agent_id,
            )
    return matches, graph_nodes


def format_answer_from_chunks(query: str, matches: list[dict[str, Any]]) -> str:
    if not matches:
        return f"No matching knowledge found for: {query}"
    lines = [f"Best available knowledge for: {query}", ""]
    for index, item in enumerate(matches[:3]):
        excerpt = re.sub(r"\s+", " ", item["content"]).strip()
        if len(excerpt) > 260:
            excerpt = excerpt[:257] + "..."
        lines.append(f"{index + 1}. {excerpt}")
    return "\n".join(lines)


def compose_chat_reply(
    query: str,
    citations: list[dict[str, Any]],
    answer: str,
    *,
    max_chars: int,
    include_scores: bool,
) -> str:
    lines = [f"Q: {query}", "", answer.strip()]
    if citations:
        lines.extend(["", "Sources:"])
        for index, item in enumerate(citations):
            source = item.get("source_uri") or item.get("document_id")
            title = item.get("title") or "Untitled"
            score_suffix = f" (score={item['score']:.3f})" if include_scores and isinstance(item.get("score"), (float, int)) else ""
            lines.append(f"{index + 1}. {title} -> {source}{score_suffix}")
    rendered = "\n".join(lines).strip()
    if len(rendered) <= max_chars:
        return rendered
    clipped = rendered[: max_chars - 3].rstrip()
    return clipped + "..."


def normalize_chat_query(query: str) -> str:
    normalized = query.strip()
    return re.sub(r"^/ask\s+", "", normalized, flags=re.IGNORECASE)


async def resolve_document_id_by_source_uri(request: Request, source_uri: str) -> UUID | None:
    row = await request.app.state.pool.fetchrow(
        "SELECT id FROM gcor.documents WHERE source_uri = $1 ORDER BY created_at DESC LIMIT 1",
        source_uri,
    )
    return row["id"] if row else None


async def link_documents_relation(request: Request, from_document_id: UUID, to_document_id: UUID, relation: str) -> None:
    async with request.app.state.pool.acquire() as connection:
        source_node = await connection.fetchval(
            "SELECT id FROM gcor.nodes WHERE document_id = $1 AND node_type = 'Document' ORDER BY created_at ASC LIMIT 1",
            from_document_id,
        )
        target_node = await connection.fetchval(
            "SELECT id FROM gcor.nodes WHERE document_id = $1 AND node_type = 'Document' ORDER BY created_at ASC LIMIT 1",
            to_document_id,
        )
        if source_node and target_node:
            await connection.execute(
                "INSERT INTO gcor.edges (source_id, target_id, relation) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                source_node, target_node, relation,
            )


@app.post("/api/ingest")
async def ingest_document(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
    file_url: Annotated[str | None, Form()] = None,
    session_json: Annotated[str | None, Form()] = None,
    attachments_json: Annotated[str | None, Form()] = None,
    file_name: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    access_level: Annotated[str, Form()] = "public",
    agent_id: Annotated[str | None, Form()] = None,
    source_uri: Annotated[str | None, Form()] = None,
    channel_name: Annotated[str | None, Form()] = None,
    channel_id: Annotated[str | None, Form()] = None,
    event_id: Annotated[str | None, Form()] = None,
    event_kind: Annotated[str | None, Form()] = None,
    event_timestamp: Annotated[str | None, Form()] = None,
    author_pubkey: Annotated[str | None, Form()] = None,
    metadata_json: Annotated[str | None, Form()] = None,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    verify_webhook(x_gcor_webhook_secret)
    provided_inputs = int(file is not None) + int(bool(text)) + int(bool(file_url)) + int(bool(session_json))
    if provided_inputs == 0:
        raise HTTPException(422, "Provide exactly one of file, text, file_url, or session_json")
    if provided_inputs > 1:
        raise HTTPException(422, "Provide only one of file, text, file_url, or session_json")
    INGESTS.inc()
    metadata = parse_metadata_json(metadata_json)
    attachments = parse_attachments_json(attachments_json)
    session_record: dict[str, Any] | None = None
    session_attachments: list[dict[str, Any]] = []

    effective_channel_name = channel_name
    effective_channel_id = channel_id
    effective_source_uri = source_uri
    effective_event_id = event_id
    effective_event_kind = event_kind
    effective_event_timestamp = event_timestamp
    effective_author_pubkey = author_pubkey

    if session_json:
        session_record = validate_chat_session_record(parse_json_object(session_json, "session_json"))
        effective_channel_name = channel_name or session_record["channel_name"]
        effective_channel_id = channel_id or session_record["channel_id"]
        effective_source_uri = source_uri or f"buzz-session://{session_record['session_id']}"
        effective_event_id = event_id or session_record["session_id"]
        effective_event_kind = event_kind or "session.replay"
        effective_event_timestamp = event_timestamp or session_record["started_at"]
        if author_pubkey is None:
            first_message = session_record["messages"][0]
            effective_author_pubkey = first_message.get("author_pubkey")
        for message in session_record["messages"]:
            for attachment in message.get("attachments", []):
                attachment_copy = dict(attachment)
                attachment_copy["message_id"] = message["message_id"]
                session_attachments.append(attachment_copy)
        metadata.update({
            "record_type": "chat_session",
            "record_format": "json",
            "session_id": session_record["session_id"],
            "message_count": len(session_record["messages"]),
            "session_schema": "schemas/chat-session.schema.json",
            "markdown_envelope_schema": "schemas/markdown-envelope.schema.json",
        })

    if file is not None:
        content = await file.read()
        media_type = file.content_type or "application/octet-stream"
        uploaded_filename = file.filename
    elif file_url:
        content, media_type, uploaded_filename = await fetch_remote_file(file_url)
    elif session_record is not None:
        bucket_name = channel_bucket_name(effective_channel_name) if effective_channel_name else MINIO_BUCKET
        await ensure_bucket_name(request.app.state.s3, bucket_name)
        session_timestamp = datetime.now(timezone.utc)
        session_digest = hashlib.sha256(session_json.encode("utf-8")).hexdigest()
        session_json_key = f"sessions/{session_timestamp:%Y/%m/%d/%H%M%S}-{session_digest[:12]}.json"
        session_markdown_body = chat_session_to_markdown(session_record)
        session_envelope = markdown_envelope(
            title or f"Session {session_record['session_id']}",
            effective_source_uri,
            access_level,
            effective_channel_name,
            effective_channel_id,
            effective_event_id,
            effective_event_kind,
            effective_event_timestamp,
            effective_author_pubkey,
            {"record_type": "chat_session", "record_format": "json"},
            session_markdown_body,
        )
        validate_json_schema(
            {
                "front_matter": {
                    "title": title or f"Session {session_record['session_id']}",
                    "source_uri": effective_source_uri,
                    "access_level": access_level,
                    "channel_name": effective_channel_name,
                    "channel_id": effective_channel_id,
                    "event_id": effective_event_id,
                    "event_kind": effective_event_kind,
                    "event_timestamp": effective_event_timestamp,
                    "author_pubkey": effective_author_pubkey,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "record_type": "chat_session",
                    "record_format": "json",
                },
                "body_markdown": session_markdown_body,
            },
            MARKDOWN_ENVELOPE_VALIDATOR,
            "session_markdown_envelope",
        )
        session_markdown_key = f"envelopes/{session_timestamp:%Y/%m/%d/%H%M%S}-{session_digest[:12]}.md"
        await __import__("asyncio").to_thread(
            request.app.state.s3.put_object,
            Bucket=bucket_name,
            Key=session_json_key,
            Body=session_json.encode("utf-8"),
            ContentType="application/json",
            Metadata={"record_type": "chat_session", "record_format": "json", "session_id": session_record["session_id"]},
        )
        await __import__("asyncio").to_thread(
            request.app.state.s3.put_object,
            Bucket=bucket_name,
            Key=session_markdown_key,
            Body=session_envelope.encode("utf-8"),
            ContentType="text/markdown",
            Metadata={"record_type": "chat_session_envelope", "session_id": session_record["session_id"]},
        )
        metadata.update({
            "session_bucket": bucket_name,
            "session_json_key": session_json_key,
            "session_markdown_key": session_markdown_key,
        })
        content = session_envelope.encode("utf-8")
        media_type = "text/markdown"
        uploaded_filename = f"{session_record['session_id']}.md"
    else:
        content = text.encode()
        media_type = "text/plain"
        uploaded_filename = None

    document_title = title or file_name or uploaded_filename or (effective_channel_name and f"{effective_channel_name} event") or "Untitled text"
    ingest_result = await ingest_payload(
        request,
        content=content,
        media_type=media_type,
        title=document_title,
        access_level=access_level,
        agent_id=agent_id,
        source_uri=effective_source_uri,
        channel_name=effective_channel_name,
        channel_id=effective_channel_id,
        event_id=effective_event_id,
        event_kind=effective_event_kind,
        event_timestamp=effective_event_timestamp,
        author_pubkey=effective_author_pubkey,
        file_url=file_url,
        file_name=file_name or uploaded_filename,
        metadata=dict(metadata),
    )

    replay_attachments = attachments + session_attachments
    if replay_attachments:
        attachment_results: list[dict[str, Any]] = []
        for index, attachment in enumerate(replay_attachments):
            attachment_url = attachment["url"]
            try:
                attachment_content, attachment_media_type, attachment_filename, attempts_used = await fetch_remote_file_with_retries(attachment_url)
                attachment_title = (
                    attachment.get("title")
                    or attachment.get("file_name")
                    or attachment.get("name")
                    or attachment_filename
                    or f"attachment-{index + 1}"
                )
                attachment_source = (
                    f"{effective_source_uri}#attachment:{index + 1}"
                    if effective_source_uri
                    else f"attachment://{effective_event_id or 'event'}:{index + 1}"
                )
                attachment_metadata = {
                    "record_type": "attachment",
                    "parent_source_uri": effective_source_uri,
                    "parent_event_id": effective_event_id,
                    "attachment_url": attachment_url,
                    "attachment_message_id": attachment.get("message_id"),
                    "attachment_name": attachment_title,
                }
                attachment_result = await ingest_payload(
                    request,
                    content=attachment_content,
                    media_type=attachment_media_type,
                    title=attachment_title,
                    access_level=access_level,
                    agent_id=agent_id,
                    source_uri=attachment_source,
                    channel_name=effective_channel_name,
                    channel_id=effective_channel_id,
                    event_id=effective_event_id,
                    event_kind="attachment.replay",
                    event_timestamp=effective_event_timestamp,
                    author_pubkey=effective_author_pubkey,
                    file_url=attachment_url,
                    file_name=attachment_filename,
                    metadata=attachment_metadata,
                )
                attachment_results.append({
                    "url": attachment_url,
                    "status": "ok",
                    "attempts": attempts_used,
                    "document_id": attachment_result["document_id"],
                    "bucket": attachment_result["bucket"],
                    "object_key": attachment_result["object_key"],
                })
            except Exception as error:  # pragma: no cover - best effort replay
                attachment_results.append({
                    "url": attachment_url,
                    "status": "error",
                    "error": str(error),
                })
        ingest_result["attachments_ingested"] = attachment_results

    return ingest_result


@app.post("/api/retrieve")
async def retrieve(
    payload: RetrieveRequest,
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    started = time.perf_counter()
    REQUESTS.inc()
    matches, graph_nodes = await run_retrieval_query(
        request,
        query=payload.query,
        top_k=payload.top_k,
        hops=payload.hops,
        access_level=payload.access_level,
        min_confidence=payload.min_confidence,
        agent_id=payload.agent_id,
        vector_weight=payload.vector_weight,
        lexical_weight=payload.lexical_weight,
        approved_only=False,
        prefer_recent_approved=False,
    )
    REQUEST_DURATION.observe(time.perf_counter() - started)
    return {
        "chunks": [dict(row) | {"id": str(row["id"]), "node_id": str(row["node_id"]), "document_id": str(row["document_id"])} for row in matches],
        "graph_nodes": [dict(row) | {"id": str(row["id"])} for row in graph_nodes],
        "reflection": "graph" if graph_nodes else ("chunks" if matches else "empty"),
    }


@app.post("/api/ask")
async def ask(
    payload: AskRequest,
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    started = time.perf_counter()
    REQUESTS.inc()
    if not payload.channel_id and not payload.channel_name:
        raise HTTPException(422, "channel scope is required: provide channel_id or channel_name")
    query = normalize_chat_query(payload.query)
    matches, graph_nodes = await run_retrieval_query(
        request,
        query=query,
        top_k=payload.top_k,
        hops=payload.hops,
        access_level=payload.access_level,
        min_confidence=payload.min_confidence,
        agent_id=payload.agent_id,
        vector_weight=payload.vector_weight,
        lexical_weight=payload.lexical_weight,
        channel_id=payload.channel_id,
        channel_name=payload.channel_name,
        approved_only=payload.approved_only,
        prefer_recent_approved=payload.prefer_recent_approved,
    )
    normalized = [dict(row) | {"id": str(row["id"]), "node_id": str(row["node_id"]), "document_id": str(row["document_id"])} for row in matches]
    citations = [
        {
            "document_id": item["document_id"],
            "title": item["title"],
            "source_uri": item.get("source_uri"),
            "ordinal": item["ordinal"],
            "score": item["score"],
        }
        for item in normalized[: payload.max_citations]
    ]
    REQUEST_DURATION.observe(time.perf_counter() - started)
    return {
        "answer": format_answer_from_chunks(query, normalized),
        "citations": citations,
        "chunks": normalized,
        "graph_nodes": [dict(row) | {"id": str(row["id"])} for row in graph_nodes],
        "reflection": "graph" if graph_nodes else ("chunks" if matches else "empty"),
    }


@app.post("/api/ask/reply")
async def ask_reply(
    payload: AskReplyRequest,
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    started = time.perf_counter()
    REQUESTS.inc()
    if not payload.channel_id and not payload.channel_name:
        raise HTTPException(422, "channel scope is required: provide channel_id or channel_name")
    query = normalize_chat_query(payload.query)
    matches, graph_nodes = await run_retrieval_query(
        request,
        query=query,
        top_k=payload.top_k,
        hops=payload.hops,
        access_level=payload.access_level,
        min_confidence=payload.min_confidence,
        agent_id=payload.agent_id,
        vector_weight=payload.vector_weight,
        lexical_weight=payload.lexical_weight,
        channel_id=payload.channel_id,
        channel_name=payload.channel_name,
        approved_only=payload.approved_only,
        prefer_recent_approved=payload.prefer_recent_approved,
    )
    normalized = [dict(row) | {"id": str(row["id"]), "node_id": str(row["node_id"]), "document_id": str(row["document_id"])} for row in matches]
    citations = [
        {
            "document_id": item["document_id"],
            "title": item["title"],
            "source_uri": item.get("source_uri"),
            "ordinal": item["ordinal"],
            "score": item["score"],
        }
        for item in normalized[: payload.max_citations]
    ]
    answer = format_answer_from_chunks(query, normalized)
    reply_text = compose_chat_reply(
        query,
        citations,
        answer,
        max_chars=payload.max_reply_chars,
        include_scores=payload.include_scores,
    )
    REQUEST_DURATION.observe(time.perf_counter() - started)
    return {
        "reply": {
            "text": reply_text,
            "channel_id": payload.channel_id,
            "channel_name": payload.channel_name,
            "source_event_id": payload.source_event_id,
        },
        "answer": answer,
        "citations": citations,
        "reflection": "graph" if graph_nodes else ("chunks" if matches else "empty"),
    }


@app.post("/api/knowledge/promote")
async def promote_knowledge(
    payload: PromoteRequest,
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    metadata = dict(payload.metadata)
    metadata.update({
        "record_type": "curated_knowledge",
        "record_format": "text",
        "promotion": True,
        "knowledge_state": "proposed",
        "knowledge_transition": "promote",
    })
    result = await ingest_payload(
        request,
        content=payload.content.encode("utf-8"),
        media_type="text/plain",
        title=payload.title,
        access_level=payload.access_level,
        agent_id=payload.agent_id,
        source_uri=payload.source_uri,
        channel_name=payload.channel_name,
        channel_id=payload.channel_id,
        event_id=payload.event_id,
        event_kind=payload.event_kind or "knowledge.promote",
        event_timestamp=payload.event_timestamp,
        author_pubkey=payload.author_pubkey,
        file_url=None,
        file_name=f"{payload.title}.txt",
        metadata=metadata,
    )
    return result | {"action": "promote"}


@app.post("/api/knowledge/correct")
async def correct_knowledge(
    payload: CorrectRequest,
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    if not payload.target_document_id and not payload.target_source_uri:
        raise HTTPException(422, "Provide target_document_id or target_source_uri")
    target_document_id: UUID | None = None
    if payload.target_document_id:
        try:
            target_document_id = UUID(payload.target_document_id)
        except ValueError as error:
            raise HTTPException(422, "target_document_id must be a valid UUID") from error
    elif payload.target_source_uri:
        target_document_id = await resolve_document_id_by_source_uri(request, payload.target_source_uri)
        if target_document_id is None:
            raise HTTPException(404, "No document found for target_source_uri")

    metadata = dict(payload.metadata)
    metadata.update({
        "record_type": "knowledge_correction",
        "record_format": "text",
        "target_document_id": str(target_document_id),
        "target_source_uri": payload.target_source_uri,
        "knowledge_state": "proposed",
        "knowledge_transition": "correct",
    })
    correction = await ingest_payload(
        request,
        content=payload.correction_text.encode("utf-8"),
        media_type="text/plain",
        title=payload.correction_title,
        access_level=payload.access_level,
        agent_id=payload.agent_id,
        source_uri=payload.source_uri,
        channel_name=payload.channel_name,
        channel_id=payload.channel_id,
        event_id=payload.event_id,
        event_kind=payload.event_kind or "knowledge.correct",
        event_timestamp=payload.event_timestamp,
        author_pubkey=payload.author_pubkey,
        file_url=None,
        file_name="knowledge-correction.txt",
        metadata=metadata,
    )
    correction_document_id = UUID(correction["document_id"])
    await link_documents_relation(request, correction_document_id, target_document_id, "CONTRADICTS")
    return correction | {
        "action": "correct",
        "target_document_id": str(target_document_id),
    }


@app.post("/api/knowledge/approve")
async def approve_knowledge(
    payload: ApproveKnowledgeRequest,
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    if not payload.target_document_id and not payload.target_source_uri:
        raise HTTPException(422, "Provide target_document_id or target_source_uri")
    target_document_id: UUID | None = None
    if payload.target_document_id:
        try:
            target_document_id = UUID(payload.target_document_id)
        except ValueError as error:
            raise HTTPException(422, "target_document_id must be a valid UUID") from error
    elif payload.target_source_uri:
        target_document_id = await resolve_document_id_by_source_uri(request, payload.target_source_uri)
        if target_document_id is None:
            raise HTTPException(404, "No document found for target_source_uri")

    approval_patch = {
        "knowledge_state": "approved",
        "knowledge_approved_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload.approved_by:
        approval_patch["knowledge_approved_by"] = payload.approved_by
    if payload.note:
        approval_patch["knowledge_approval_note"] = payload.note

    row = await request.app.state.pool.fetchrow(
        """
        UPDATE gcor.documents
        SET updated_at = now(),
            metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb
        WHERE id = $1
        RETURNING id, title, source_uri, metadata
        """,
        target_document_id,
        json.dumps(approval_patch),
    )
    if row is None:
        raise HTTPException(404, "Knowledge document not found")
    row_metadata = row["metadata"]
    if isinstance(row_metadata, str):
        try:
            row_metadata = json.loads(row_metadata)
        except json.JSONDecodeError:
            row_metadata = {}
    if not isinstance(row_metadata, dict):
        row_metadata = {}
    return {
        "action": "approve",
        "document_id": str(row["id"]),
        "title": row["title"],
        "source_uri": row["source_uri"],
        "knowledge_state": row_metadata.get("knowledge_state"),
    }


@app.post("/api/knowledge/transition")
async def transition_knowledge(
    payload: TransitionKnowledgeRequest,
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    if not payload.target_document_id and not payload.target_source_uri:
        raise HTTPException(422, "Provide target_document_id or target_source_uri")
    target_document_id: UUID | None = None
    if payload.target_document_id:
        try:
            target_document_id = UUID(payload.target_document_id)
        except ValueError as error:
            raise HTTPException(422, "target_document_id must be a valid UUID") from error
    elif payload.target_source_uri:
        target_document_id = await resolve_document_id_by_source_uri(request, payload.target_source_uri)
        if target_document_id is None:
            raise HTTPException(404, "No document found for target_source_uri")

    patch: dict[str, Any] = {
        "knowledge_state": payload.transition,
        "knowledge_transition": payload.transition,
        "knowledge_transition_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload.changed_by:
        patch["knowledge_transition_by"] = payload.changed_by
    if payload.note:
        patch["knowledge_transition_note"] = payload.note

    if payload.transition == "approved":
        patch["knowledge_approved_at"] = patch["knowledge_transition_at"]
        if payload.changed_by:
            patch["knowledge_approved_by"] = payload.changed_by
        if payload.note:
            patch["knowledge_approval_note"] = payload.note

    if payload.transition == "superseded":
        if not payload.superseded_by_document_id and not payload.superseded_by_source_uri:
            raise HTTPException(422, "superseded transition requires superseded_by_document_id or superseded_by_source_uri")
        superseded_by_document_id: UUID | None = None
        if payload.superseded_by_document_id:
            try:
                superseded_by_document_id = UUID(payload.superseded_by_document_id)
            except ValueError as error:
                raise HTTPException(422, "superseded_by_document_id must be a valid UUID") from error
        else:
            superseded_by_document_id = await resolve_document_id_by_source_uri(request, payload.superseded_by_source_uri or "")
            if superseded_by_document_id is None:
                raise HTTPException(404, "No document found for superseded_by_source_uri")
        patch["superseded_by_document_id"] = str(superseded_by_document_id)
        patch["superseded_by_source_uri"] = payload.superseded_by_source_uri
        await link_documents_relation(request, target_document_id, superseded_by_document_id, "RELATES_TO")

    row = await request.app.state.pool.fetchrow(
        """
        UPDATE gcor.documents
        SET updated_at = now(),
            metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb
        WHERE id = $1
        RETURNING id, title, source_uri, metadata
        """,
        target_document_id,
        json.dumps(patch),
    )
    if row is None:
        raise HTTPException(404, "Knowledge document not found")
    row_metadata = row["metadata"]
    if isinstance(row_metadata, str):
        try:
            row_metadata = json.loads(row_metadata)
        except json.JSONDecodeError:
            row_metadata = {}
    if not isinstance(row_metadata, dict):
        row_metadata = {}
    return {
        "action": "transition",
        "document_id": str(row["id"]),
        "title": row["title"],
        "source_uri": row["source_uri"],
        "knowledge_state": row_metadata.get("knowledge_state"),
        "knowledge_transition": row_metadata.get("knowledge_transition"),
    }


@app.get("/api/collections")
async def collections(
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    rows = await request.app.state.pool.fetch(
        """SELECT d.id, d.title, d.source_uri, d.media_type, d.access_level, d.created_at, count(c.id) AS chunk_count
           FROM gcor.documents d LEFT JOIN gcor.chunks c ON c.document_id = d.id
           GROUP BY d.id ORDER BY d.created_at DESC"""
    )
    return [{**dict(row), "id": str(row["id"])} for row in rows]


@app.get("/api/capabilities")
async def capabilities(
    request: Request,
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    return dict(await request.app.state.pool.fetchrow("SELECT * FROM gcor.extension_capabilities"))


@app.get("/api/examples/session-json")
async def session_json_example(
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    sample = sample_chat_session()
    validate_json_schema(sample, CHAT_SESSION_VALIDATOR, "session_json_example")
    return {
        "schema": "schemas/chat-session.schema.json",
        "example": sample,
    }


@app.get("/api/examples/markdown-envelope")
async def markdown_envelope_example(
    x_gcor_webhook_secret: Annotated[str | None, Header()] = None,
):
    verify_stack_api_secret(x_gcor_webhook_secret)
    sample = sample_markdown_envelope()
    validate_json_schema(sample, MARKDOWN_ENVELOPE_VALIDATOR, "markdown_envelope_example")
    return {
        "schema": "schemas/markdown-envelope.schema.json",
        "example": sample,
    }


@app.get("/health")
async def health(request: Request):
    await request.app.state.pool.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)