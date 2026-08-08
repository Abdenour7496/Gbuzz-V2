import base64
import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

PROXY_URL = os.getenv("GCOR_PROXY_URL", "http://gcor-proxy:5001").rstrip("/")
WEBHOOK_SECRET = os.getenv("INGEST_WEBHOOK_SECRET", "")
STACK_API_SECRET = os.getenv("STACK_API_SECRET", "") or WEBHOOK_SECRET

mcp = FastMCP("mcp-postgres-gcor")


async def proxy_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"X-Gcor-Webhook-Secret": STACK_API_SECRET} if STACK_API_SECRET else {}
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(f"{PROXY_URL}{path}", json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def semantic_search(
    query: str,
    top_k: int = 8,
    access_level: str = "public",
    min_confidence: float = 0.0,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Find semantically relevant GCOR document chunks subject to ACL and confidence filters."""
    return await proxy_post("/api/retrieve", {
        "query": query, "top_k": top_k, "hops": 0, "access_level": access_level,
        "min_confidence": min_confidence, "agent_id": agent_id,
    })


@mcp.tool()
async def graph_expand(
    query: str,
    top_k: int = 8,
    hops: int = 2,
    access_level: str = "public",
    min_confidence: float = 0.0,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Run GCOR semantic retrieval followed by bounded graph expansion around matching chunks."""
    return await proxy_post("/api/retrieve", {
        "query": query, "top_k": top_k, "hops": hops, "access_level": access_level,
        "min_confidence": min_confidence, "agent_id": agent_id,
    })


@mcp.tool()
async def ask_knowledge(
    query: str,
    top_k: int = 6,
    hops: int = 1,
    access_level: str = "public",
    min_confidence: float = 0.0,
    agent_id: str | None = None,
    max_citations: int = 4,
    channel_id: str | None = None,
    channel_name: str | None = None,
    approved_only: bool = True,
    prefer_recent_approved: bool = True,
) -> dict[str, Any]:
    """Ask GCOR for a grounded answer with citations from indexed knowledge."""
    return await proxy_post("/api/ask", {
        "query": query,
        "top_k": top_k,
        "hops": hops,
        "access_level": access_level,
        "min_confidence": min_confidence,
        "agent_id": agent_id,
        "max_citations": max_citations,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "approved_only": approved_only,
        "prefer_recent_approved": prefer_recent_approved,
    })


@mcp.tool()
async def ask_knowledge_reply(
    query: str,
    top_k: int = 6,
    hops: int = 1,
    access_level: str = "public",
    min_confidence: float = 0.0,
    agent_id: str | None = None,
    max_citations: int = 4,
    max_reply_chars: int = 1800,
    include_scores: bool = False,
    channel_id: str | None = None,
    channel_name: str | None = None,
    source_event_id: str | None = None,
    approved_only: bool = True,
    prefer_recent_approved: bool = True,
) -> dict[str, Any]:
    """Ask GCOR and return a workflow-friendly reply envelope for posting back into chat."""
    return await proxy_post("/api/ask/reply", {
        "query": query,
        "top_k": top_k,
        "hops": hops,
        "access_level": access_level,
        "min_confidence": min_confidence,
        "agent_id": agent_id,
        "max_citations": max_citations,
        "max_reply_chars": max_reply_chars,
        "include_scores": include_scores,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "source_event_id": source_event_id,
        "approved_only": approved_only,
        "prefer_recent_approved": prefer_recent_approved,
    })


@mcp.tool()
async def ingest_document(
    content: str,
    title: str = "MCP document",
    access_level: str = "public",
    agent_id: str | None = None,
    source_uri: str | None = None,
    channel_name: str | None = None,
    channel_id: str | None = None,
    event_id: str | None = None,
    event_kind: str | None = None,
    event_timestamp: str | None = None,
    author_pubkey: str | None = None,
    file_name: str | None = None,
    file_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    base64_encoded: bool = False,
) -> dict[str, Any]:
    """Ingest text/session content into GCOR with optional channel metadata and attachment replay."""
    text = content
    if base64_encoded:
        try:
            text = base64.b64decode(content, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("base64_encoded content must decode to UTF-8 text") from error
    form: dict[str, Any] = {"text": text, "title": title, "access_level": access_level}
    if agent_id:
        form["agent_id"] = agent_id
    if source_uri:
        form["source_uri"] = source_uri
    if channel_name:
        form["channel_name"] = channel_name
    if channel_id:
        form["channel_id"] = channel_id
    if event_id:
        form["event_id"] = event_id
    if event_kind:
        form["event_kind"] = event_kind
    if event_timestamp:
        form["event_timestamp"] = event_timestamp
    if author_pubkey:
        form["author_pubkey"] = author_pubkey
    if file_name:
        form["file_name"] = file_name
    if file_url:
        form["file_url"] = file_url
    if metadata:
        form["metadata_json"] = json.dumps(metadata)
    if session:
        form["session_json"] = json.dumps(session)
        form.pop("text", None)
    if attachments:
        form["attachments_json"] = json.dumps(attachments)
    headers = {"X-Gcor-Webhook-Secret": STACK_API_SECRET} if STACK_API_SECRET else {}
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(f"{PROXY_URL}/api/ingest", data=form, headers=headers)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def promote_knowledge(
    title: str,
    content: str,
    access_level: str = "public",
    agent_id: str | None = None,
    source_uri: str | None = None,
    channel_name: str | None = None,
    channel_id: str | None = None,
    event_id: str | None = None,
    event_kind: str | None = None,
    event_timestamp: str | None = None,
    author_pubkey: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote curated content into durable GCOR knowledge memory."""
    return await proxy_post("/api/knowledge/promote", {
        "title": title,
        "content": content,
        "access_level": access_level,
        "agent_id": agent_id,
        "source_uri": source_uri,
        "channel_name": channel_name,
        "channel_id": channel_id,
        "event_id": event_id,
        "event_kind": event_kind,
        "event_timestamp": event_timestamp,
        "author_pubkey": author_pubkey,
        "metadata": metadata or {},
    })


@mcp.tool()
async def correct_knowledge(
    correction_text: str,
    target_document_id: str | None = None,
    target_source_uri: str | None = None,
    correction_title: str = "Knowledge correction",
    access_level: str = "public",
    agent_id: str | None = None,
    source_uri: str | None = None,
    channel_name: str | None = None,
    channel_id: str | None = None,
    event_id: str | None = None,
    event_kind: str | None = None,
    event_timestamp: str | None = None,
    author_pubkey: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit a correction against existing knowledge with explicit target linkage."""
    return await proxy_post("/api/knowledge/correct", {
        "correction_text": correction_text,
        "target_document_id": target_document_id,
        "target_source_uri": target_source_uri,
        "correction_title": correction_title,
        "access_level": access_level,
        "agent_id": agent_id,
        "source_uri": source_uri,
        "channel_name": channel_name,
        "channel_id": channel_id,
        "event_id": event_id,
        "event_kind": event_kind,
        "event_timestamp": event_timestamp,
        "author_pubkey": author_pubkey,
        "metadata": metadata or {},
    })


@mcp.tool()
async def approve_knowledge(
    target_document_id: str | None = None,
    target_source_uri: str | None = None,
    approved_by: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Approve proposed knowledge so it becomes visible in approved-only ask flows."""
    return await proxy_post("/api/knowledge/approve", {
        "target_document_id": target_document_id,
        "target_source_uri": target_source_uri,
        "approved_by": approved_by,
        "note": note,
    })


@mcp.tool()
async def transition_knowledge(
    transition: str,
    target_document_id: str | None = None,
    target_source_uri: str | None = None,
    changed_by: str | None = None,
    note: str | None = None,
    superseded_by_document_id: str | None = None,
    superseded_by_source_uri: str | None = None,
) -> dict[str, Any]:
    """Transition knowledge lifecycle state (approved, superseded, archived, rejected, proposed)."""
    return await proxy_post("/api/knowledge/transition", {
        "transition": transition,
        "target_document_id": target_document_id,
        "target_source_uri": target_source_uri,
        "changed_by": changed_by,
        "note": note,
        "superseded_by_document_id": superseded_by_document_id,
        "superseded_by_source_uri": superseded_by_source_uri,
    })


if __name__ == "__main__":
    mcp.run(transport="sse")