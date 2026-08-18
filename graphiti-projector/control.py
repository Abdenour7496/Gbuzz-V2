import argparse
import asyncio
import json
from typing import Any

import asyncpg
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import main


COUNT_SQL = """
SELECT
  count(*) FILTER (WHERE status='pending')::int AS pending,
  count(*) FILTER (WHERE status='failed' AND attempts < $1)::int AS retryable,
  count(*) FILTER (WHERE (status='processing' AND attempts < $1)
                       OR (status='submitted' AND reconciled_at IS NULL))::int AS in_flight,
  count(*) FILTER (WHERE status IN ('failed','processing') AND attempts >= $1)::int AS dead_letter
FROM gcor.graphiti_projection
"""


async def counts(pool: asyncpg.Pool) -> dict[str, int]:
    row = await pool.fetchrow(COUNT_SQL, main.MAX_ATTEMPTS)
    return {name: int(row[name]) for name in ("pending", "retryable", "in_flight", "dead_letter")}


async def reconcile(pool: asyncpg.Pool) -> int:
    async with (
        httpx.AsyncClient(headers={"Host": main.GRAPHITI_MCP_HOST_HEADER}, follow_redirects=True) as client,
        streamable_http_client(main.GRAPHITI_MCP_URL, http_client=client) as streams,
    ):
        read_stream, write_stream, _ = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tool, accepted = main.choose_episode_list_tool((await session.list_tools()).tools)
            return await main.reconcile_episodes(pool, session, tool, accepted)


async def bounded_requeue(pool: asyncpg.Pool, limit: int) -> list[str]:
    # Projection bookkeeping only. Submitted work is deliberately excluded until
    # reconciliation or normal submission expiry proves it is safe to retry.
    rows = await pool.fetch(
        """WITH candidates AS (
               SELECT entry_id FROM gcor.graphiti_projection
               WHERE status='failed'
               ORDER BY updated_at,entry_id
               FOR UPDATE SKIP LOCKED LIMIT $2
           )
           UPDATE gcor.graphiti_projection p
           SET status='pending',attempts=CASE WHEN p.attempts >= $1 THEN 0 ELSE p.attempts END,
               error=NULL,next_attempt_at=now(),updated_at=now()
           FROM candidates c WHERE p.entry_id=c.entry_id
           RETURNING p.entry_id::text""",
        main.MAX_ATTEMPTS, limit,
    )
    return [row["entry_id"] for row in rows]


async def run(args: argparse.Namespace) -> int:
    pool = await asyncpg.create_pool(**main.POSTGRES, min_size=1, max_size=2)
    try:
        before = await counts(pool)
        result: dict[str, Any] = {"before": before, "authoritative_writes": 0}
        if args.command == "replay":
            result["reconciled"] = await reconcile(pool)
            result["requeued_entry_ids"] = await bounded_requeue(pool, args.limit)
            result["limit"] = args.limit
        result["after"] = await counts(pool)
        result["passed"] = all(value == 0 for value in result["after"].values())
        print(json.dumps(result, separators=(",", ":")))
        return 0 if (args.command == "replay" or result["passed"]) else 1
    finally:
        await pool.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect or safely requeue Graphiti projection bookkeeping")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gate", help="fail unless all unfinished projection buckets are zero")
    replay = sub.add_parser("replay", help="reconcile first, then requeue a bounded dead-letter batch")
    replay.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1..100")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
