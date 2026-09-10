# Transactional governance and outbound fetch restrictions

Implemented 8 September 2026: production roadmap items 2 and 3. The running local
services were left unchanged; verification used a separate Docker project.

## Transactional governance

Approval and lifecycle transitions commit document metadata, applicable Graphiti
queue updates, supersession edges, and an audit event in one PostgreSQL
transaction. Any failure rolls those writes back together.

A background publisher copies committed events to MinIO. Storage failures leave
events queued for exponential retry, capped at five minutes between attempts,
without a retry limit. PostgreSQL row locks and `SKIP LOCKED` coordinate publishers.
Stable event IDs/object keys and conditional creation prevent duplicate versions
after a crash between object publication and database acknowledgment. Existing
artifact digests are checked; conflicting objects are never overwritten.

Existing request and response fields remain supported. Responses also contain
`governance_event_id` and `governance_publication_status: pending`. Success means
the operation and audit event are durable in PostgreSQL; MinIO publication is now
eventual. Clients that need the file immediately must wait for publication.

Send an optional `Idempotency-Key` header (1–200 characters) to approval or
transition endpoints. The same operation/payload/key returns the original receipt;
reuse with another payload returns 409. Keys are stack-scoped and retained, so
generate unique keys and reuse them only for retries. Without a key, each call
remains a distinct operation. Replayed receipts describe the original commit,
not the current publication state.

Authenticated inspection endpoints:

- `GET /api/governance/outbox`: pending/published/retrying counts and oldest age.
- `GET /api/governance/events/{event_id}`: publication state, attempts, next retry,
  sanitized last error, and object location.

MCP approval and transition tools accept optional `idempotency_key` arguments.
`get_governance_outbox` reports the backlog. SSE transport and credentials remain
unchanged. Prometheus exports `gcor_governance_pending_events`,
`gcor_governance_retrying_events`, and `gcor_governance_oldest_pending_seconds`;
new alerts report sustained retries and publication delay.

## Outbound fetch policy

Remote file/attachment fetching uses a dedicated transport. Each TCP connection
resolves and validates the complete DNS answer set, then connects only to a checked
numeric address. HTTP Host, TLS SNI, and certificate verification retain the
original URL hostname. There is no unrestricted hostname fallback.

- Private, loopback, link-local/metadata, shared-address, multicast, reserved, and
  selected IPv6 transition destinations are rejected by default.
- Mixed public/private DNS answers fail closed. Connection failure may try another
  address from the same validated set.
- Every redirect is checked; existing host allowlist, redirect, byte, and time
  limits remain enforced.
- Environment proxies are ignored for these fetches. URL credentials and malformed
  ports are rejected.

`REMOTE_FETCH_BLOCK_PRIVATE_HOSTS` now defaults to `true` in the application and
base Compose; the production overlay already enforces it. Explicit `false` is
retained for trusted local compatibility and must not be a production fallback.
Narrow `REMOTE_FETCH_ALLOWED_HOSTS` to known source domains where possible.

Internal database, MinIO, Ollama, and MCP/Graphiti clients retain their existing
connections. This is an enforced application fetch boundary, not a container-wide
firewall. Deployment network controls still need to constrain other clients and
contain arbitrary code execution.

## Migration and fallback

Back up PostgreSQL through the existing deployment procedure. Apply
`migrations/0007_governance_outbox.sql` after migrations 0001–0006, then deploy the
proxy and MCP images. This additive migration creates a table and indexes without
rewriting existing knowledge. For an existing GCOR schema using base Compose:

```powershell
docker compose run --rm --no-deps gcor-migrate /bin/sh -ec 'psql -v ON_ERROR_STOP=1 -f /migrations/0007_governance_outbox.sql'
```

The guarded local proxy rollout checks this migration before replacing a container.
It does not apply migrations or start missing dependencies. Production lock
deployments should continue through their approved image-promotion workflow.

Without the migration, the new proxy retains its original health/read paths,
rejects governance writes with 503 before mutation, and reports
`governance_schema: migration_required` in readiness. Restart GCOR after applying
a previously missing migration; schema availability is detected at startup.
There is no automatic fallback to nontransactional governance.

Retain the outbox and queued events during application rollback. Events resume
publication when an outbox-capable proxy starts. If reverting to a binary without
outbox support, pause governance writes: that version cannot publish queued events
or provide the new transaction guarantees. Never drop/truncate the outbox to clear
an alert. Published events and idempotency keys have no automatic cleanup policy.

## Verification

- 56 unit tests passed across the proxy, projectors, recovery controller, and MCP.
- Seven real PostgreSQL/MinIO fault tests passed: graph-update rollback, outbox-insert
  rollback, concurrent idempotent retries, publication failure/recovery, interrupted
  publication without another object version, valid supersession, and invalid supersession rollback.
- Isolated HTTP integration passed, including publication, idempotency conflicts,
  and rejection of an internal MinIO fetch URL.
- Governance committed during a real MinIO outage. The event published after
  stopping the API, restoring MinIO, and restarting the API.
- Five mocked rollout scenarios passed, including missing-migration refusal.

Identity-derived authorization and downstream Graphiti extraction/reconciliation
guarantees remain outside these two changes. Graphiti queue changes are now atomic
with governance; its downstream processing remains asynchronous.
