# Production safeguards and guarded rollout

The initial safeguards release adds bounded API admission, bounded uploads and attachment fetching,
dependency readiness, and a proxy-only rollout with image rollback. It changes no
database schema, existing credentials, knowledge records, model selection, or MCP
tool signatures. The subsequent [governance and egress release](governance-and-egress.md)
adds migration 0007 and optional MCP idempotency arguments. Read its deployment
requirements before using the guarded rollout with the current source.

## Runtime privilege separation

Migration 0011 creates the `gcor_app` privilege role: data access on every `gcor`
table and sequence (including tables created by later migrations), read-only access
to the Buzz relay tables in `public`, no ownership, no DDL, `NOSUPERUSER`,
`NOBYPASSRLS`, and no write access to `gcor.schema_migrations`. When `.env` sets
`GCOR_DB_USER` and `GCOR_DB_PASSWORD`, `gcor-migrate` creates or refreshes that
login role as a member of `gcor_app` after each migration pass, and `gcor-proxy`,
`gcor-event-projector`, `graphiti-projector` and the services that extend the proxy
(`buzz-knowledge`, `gcor-enterprise`) connect as it. Migrations keep running as the
schema owner (`POSTGRES_USER`). Leave both variables empty to keep the previous
single-user behaviour. The CI integration stack runs every lifecycle test as the
restricted role; test fixtures that seed relay tables use `POSTGRES_ADMIN_USER`.

Verify on a deployment:

```powershell
docker compose exec gcor-proxy python -c "import asyncio,asyncpg,main; print(asyncio.run(asyncpg.connect(host=main.POSTGRES_HOST,user=main.POSTGRES_USER,password=main.POSTGRES_PASSWORD,database=main.POSTGRES_DB).fetchrow('SELECT current_user, rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname=current_user')))"
```

Expect `rolsuper`, `rolbypassrls` and `rolcreaterole` all `False`.

Migration 0012 adds row-level security on `gcor.documents`, `gcor.chunks` and
`gcor.nodes` for `gcor_app`. `proxy/db_scope.py` wraps the connection pool: while a
signed Buzz identity is active, every connection carries `gcor.channel_id` and
`gcor.access_level` session settings, and the policies restrict reads and writes to
that channel and access level; asyncpg's `RESET ALL` on release clears the scope.
Connections without an identity (legacy shared-secret API, workers, migrations) see
every row, and table owners bypass RLS entirely, so the policies only take effect once
services connect as the runtime role. Server-side channel and actor checks remain the
primary authorization boundary; RLS catches a query that omits them. CI runs
`tests/integration/rls_scope.py`, which issues deliberately unfiltered SQL through the
same pool wrapper and asserts isolation, refused cross-channel writes and scope reset.

## Infrastructure boundaries

- **Object store.** With `GCOR_S3_ACCESS_KEY`, `GCOR_S3_SECRET_KEY` and
  `GCOR_CHANNEL_BUCKET_PREFIX` set, `minio-init` creates a MinIO user whose policy
  allows bucket listing plus full access to `GCOR_S3_BUCKET` and buckets named
  `<prefix>*` only; `gcor-proxy` (and the services that extend it) use that user
  instead of the root keys, which remain with the relay and the initializer. Channel
  buckets are created with the prefix. The proxy skips buckets it may not administer
  when enabling versioning. Existing deployments with unprefixed channel buckets
  must copy them to prefixed names (`mc mirror`) before enabling scoped keys, or keep
  the root keys. This path is validated by Compose rendering and unit tests; verify
  the policy on a live stack with `mc admin user info local gcor-service`.
- **Networks.** `data-net` (internal) carries PostgreSQL, Redis, MinIO and FalkorDB;
  only the relay, GCOR services, migrations and exporters join it. `control-net`
  (internal) carries the Docker socket proxy and the recovery controller. Internal
  networks have no egress and cannot publish ports; MinIO also joins `buzz-net` for
  its localhost console. Changing networks recreates containers; volumes persist.
- **Docker Engine.** Only `docker-socket-proxy` mounts the socket, with
  `CONTAINERS=1 ALLOW_RESTARTS=1 POST=0`. The controller reaches it via
  `DOCKER_HOST=tcp://docker-socket-proxy:2375` and issues only container listing,
  inspection and `restart`; any other call returns 403 and is audited as a failed
  action.

## Limits

Configure these in `.env`; Compose passes them to the proxy. Defaults:

| Setting | Default | Behavior |
| --- | --- | --- |
| `MAX_INGEST_FILE_BYTES` | 26214400 (25 MiB) | File/content ceiling; uploaded reads stop at the limit plus one byte. |
| `MAX_REQUEST_BODY_BYTES` | 27262976 (26 MiB) | Whole API request, including multipart overhead; 413 before parsing or application writes. Actual bytes are checked even without an accurate Content-Length. |
| `MAX_API_IN_FLIGHT` | 4 | Concurrent API requests per proxy process. Excess requests receive 429 with `Retry-After: 2`; health/metrics remain accessible. |
| `REQUEST_BODY_TIMEOUT_SECONDS` | 60 | Time allowed to receive an API body; 408 before work starts. Does not cancel a running database mutation. |
| `MAX_QUERY_CHARS` | 10000 | Ask/retrieve query ceiling; 422 on violation. |
| `MAX_ATTACHMENTS_PER_REQUEST` | 100 | Combined explicit and session attachments, validated before document writes. |
| `MAX_ATTACHMENT_TOTAL_BYTES` | 104857600 (100 MiB) | Shared decoded download-byte budget for attachment replay, including failed attempts and retries. |
| `ATTACHMENT_REPLAY_BUDGET_SECONDS` | 120 | Wall-clock window for starting/completing attachment fetches. Ingestion already underway is allowed to finish. |
| `REMOTE_FETCH_TOTAL_TIMEOUT_SECONDS` | 60 | Deadline for each fetch attempt, including validation, redirects, and streaming. |

Remote attachment replay remains best-effort: the parent document and successful
attachments remain stored, and failed attachments appear in `attachments_ingested`
with `status: error`. Budget exhaustion prevents further downloads for that request.
The existing retry policy still handles transient network failures.

Raise limits explicitly if a trusted workload needs more capacity. Keep whole-body
limits above the file limit to allow multipart overhead. The admission limit is
per process, not a distributed or identity-based rate limiter; API bodies are
buffered within this bound before parsing. Account for parser, embedding, and PDF
processing memory when increasing concurrency. Set ingress and container limits
as well. Do not disable authentication as a compatibility fallback.

## Health and recovery behavior

- `/health` retains its existing PostgreSQL check and remains the Docker healthcheck.
- `/health/live` reports process responsiveness without dependency checks.
- `/health/ready` checks PostgreSQL, the configured MinIO bucket, and governance
  migration availability, returning 200
  or 503 with sanitized dependency states. Probes are shared and cached for five
  seconds; callers wait at most three seconds. Slow storage cannot spawn a new
  probe thread on every health request. Inference/model readiness is not checked.

Keep Docker on the existing endpoint until your operational policy is reviewed.
A shared storage failure should not trigger a restart loop across all clients.
Use readiness for ingress admission or rollout checks. Readiness recovers once
dependencies recover; allow for the short cache window.

## Guarded rollout and fallback

The current local stack has no running GCOR proxy, so this release was verified
in an isolated stack and was not applied to the live relay/database services.

For an existing healthy proxy using the base Compose configuration:

```powershell
./scripts/deploy-gcor-safeguards.ps1 -CheckOnly
./scripts/deploy-gcor-safeguards.ps1
```

The script checks governance migration availability, shares the stack updater's mutex, retains the old image, builds a
candidate, replaces only `gcor-proxy` using `--no-deps`, and checks readiness. A
failed rollout restores the previous image and checks the original health
endpoint. It exits with an error after rollback so automation cannot mistake a
failed deployment for success. Build failure leaves the running container alone.
If rollback fails too, it reports the retained image for operator recovery.

The selected image remains in ignored
`docker-compose.safeguards.override.json` (gitignored deployment state that pins the
currently deployed, locally built proxy image; `docker-compose.safeguards.override.example.json`
is the tracked shape). Preserve this overlay in subsequent
Compose operations until promoting an image into your normal release workflow:

```powershell
docker compose -f docker-compose.yml -f docker-compose.safeguards.override.json ps
```

The script refuses unknown production/observability overlays and an absent proxy;
it cannot infer deployment settings or perform initial migrations safely. Use the
existing initial setup instructions when GCOR has never been started. For a
production lock deployment, use its approved image-promotion workflow and retain
the prior lock for rollback. The current source also requires additive governance
migration 0007 before rollout; see the linked governance release notes.

## Verification

- 43 Python tests passed, including 16 new safeguard regressions.
- Isolated Docker ingestion/deduplication/retrieval/recovery smoke test passed,
  including the new liveness and dependency-readiness endpoints.
- Isolated MinIO outage: readiness returned 503, liveness stayed 200, and
  readiness recovered automatically after MinIO restarted.
- Four mocked Docker rollout scenarios passed: absent proxy, successful rollout,
  build failure without recreation, and recreation failure with automatic rollback.
  A live image rollback has not been exercised.

Transactional governance and DNS-bound fetch restrictions are now implemented in
the linked follow-up release. Identity-derived authorization, durable ingestion jobs,
model evaluation, and restore/retention workflows remain in the roadmap. These safeguards do not make the
shared-secret API suitable for untrusted multi-tenant access.
