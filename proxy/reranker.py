"""Optional local cross-encoder reranking of retrieval candidates.

Disabled unless RERANKER_URL is set. The endpoint speaks the common rerank wire format served by
llama.cpp (`llama-server --reranking`, POST /v1/rerank), Jina/Cohere-compatible servers and
Hugging Face text-embeddings-inference (POST /rerank). Reranking is fail-open: any error, timeout
or malformed reply returns None and retrieval keeps its fused order, so the reranker can never make
retrieval unavailable or return evidence the database query did not already authorise. It only
reorders rows it is given.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
from prometheus_client import Counter, Histogram

RERANKER_URL = os.getenv("RERANKER_URL", "").strip()
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "bge-reranker-v2-m3")
RERANK_CANDIDATES = max(1, int(os.getenv("RERANK_CANDIDATES", "12")))
RERANK_TIMEOUT_SECONDS = float(os.getenv("RERANK_TIMEOUT_SECONDS", "3"))
RERANK_MAX_CHARS = max(200, int(os.getenv("RERANK_MAX_CHARS", "1000")))
# After a failure, skip the reranker for this long so a slow or broken reranker does not add its
# timeout to every request.
RERANK_COOLDOWN_SECONDS = float(os.getenv("RERANK_COOLDOWN_SECONDS", "30"))
_skip_until = 0.0

RERANKS = Counter("gcor_rerank_total", "Reranker calls by outcome", ["outcome"])
RERANK_DURATION = Histogram("gcor_rerank_duration_seconds", "Reranker call duration")


def enabled() -> bool:
    return bool(RERANKER_URL)


def document_text(row: Any) -> str:
    title = (row.get("title") if hasattr(row, "get") else row["title"]) or ""
    content = row["content"] or ""
    text = f"{title}\n{content}" if title else content
    return text[:RERANK_MAX_CHARS]


def parse_scores(payload: Any, count: int) -> list[float] | None:
    """Accept {"results": [{"index", "relevance_score"|"score"}]} or a bare list of the same."""
    items = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return None
    scores: list[float | None] = [None] * count
    for item in items:
        if not isinstance(item, dict):
            return None
        index = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
            return None
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            return None
        scores[index] = float(score)
    if any(score is None for score in scores):
        return None
    return scores  # type: ignore[return-value]


async def scores(query: str, documents: list[str]) -> list[float] | None:
    global _skip_until
    if not RERANKER_URL or not documents:
        return None
    if time.monotonic() < _skip_until:
        RERANKS.labels("skipped").inc()
        return None
    body = {"model": RERANKER_MODEL, "query": query, "documents": documents, "texts": documents,
            "top_n": len(documents), "return_documents": False}
    try:
        with RERANK_DURATION.time():
            async with httpx.AsyncClient(timeout=RERANK_TIMEOUT_SECONDS, trust_env=False) as client:
                response = await client.post(RERANKER_URL, json=body)
                response.raise_for_status()
                result = parse_scores(response.json(), len(documents))
    except Exception:
        RERANKS.labels("error").inc()
        _skip_until = time.monotonic() + RERANK_COOLDOWN_SECONDS
        return None
    if result is None:
        RERANKS.labels("malformed").inc()
        _skip_until = time.monotonic() + RERANK_COOLDOWN_SECONDS
        return None
    RERANKS.labels("ok").inc()
    return result


def reorder(rows: list[Any], relevance: list[float] | None) -> list[Any]:
    """Stable sort by reranker relevance; ties keep the fused order."""
    if relevance is None or len(relevance) != len(rows):
        return list(rows)
    order = sorted(range(len(rows)), key=lambda i: (-relevance[i], i))
    return [rows[i] for i in order]


def cap_per_document(rows: list[Any], limit: int, per_document: int) -> list[Any]:
    """Keep at most `per_document` chunks of any one document, preserving order."""
    if per_document <= 0:
        return list(rows[:limit])
    kept: list[Any] = []
    counts: dict[Any, int] = {}
    for row in rows:
        key = row["document_id"]
        if counts.get(key, 0) >= per_document:
            continue
        counts[key] = counts.get(key, 0) + 1
        kept.append(row)
        if len(kept) >= limit:
            break
    return kept
