# Production blockers: knowledge fidelity, speed and resilience (2026-10-22)

Scope agreed for this release: a self-contained, single-server lab deployment that generates
knowledge from human–agent collaboration, ingests it and retrieves it with high fidelity, accuracy
and speed. High availability and multi-host resilience are out of scope.

All results below were measured on an isolated replica (2 vCPU, 8 GB) running this branch's code
unmodified against PostgreSQL 16 + pgvector 0.6.2, with a substitute embedding model and no
generation model; see the 2026-10-21 feasibility assessment for the method and its limits. The
deployed lab stack was not changed.

## What changed

| Blocker | Change | Files |
| --- | --- | --- |
| Approval bypass through a service credential | `/api/ingest` strips every `knowledge_*` lifecycle/authority field and supersession/invalidation markers from caller metadata; only the governance paths can set them. Workload tokens accept an optional `"channels": [...]` allowlist. | `proxy/main.py`, `proxy/access_policy.py`, `tests/test_ingest_governance.py` |
| Retrieval collapsed with volume | Migration 0016: stored generated `documents.channel_id` (plain-text equality is index-eligible under RLS; `metadata->>'channel_id'` is not), index on `(channel_id, access_level)`, index on `nodes(document_id)`. Graph expansion now walks out from the seeds and authorises each visited node, instead of materialising every node of the channel. Evidence-validity check is skipped inline for documents that carry no Buzz evidence. Per-connection `statement_timeout`. | `migrations/0016_retrieval_performance.sql`, `proxy/main.py`, `proxy/graph_retrieval.py`, `proxy/enterprise_workflows.py`, `proxy/buzz_wiki.py` |
| Requests rejected above 4 concurrent | Bounded FIFO admission queue (`MAX_API_QUEUED`, `API_QUEUE_SECONDS`) in front of `MAX_API_IN_FLIGHT`. | `proxy/request_limits.py` |
| Retrieval fidelity | Keyword leg matches any query term, weights titles above body text, and is fused with the vector leg by reciprocal rank (default weights 0.5/0.5). Abstention floor: no keyword overlap and cosine below `RETRIEVAL_MIN_VECTOR_SCORE` returns no evidence and the exact abstention `No authorized evidence was found.` recognised by `scripts/evaluate-knowledge.py`. | `proxy/main.py` |
| Ingestion jobs failed permanently after ~1 minute of outage | 12 attempts with exponential backoff (15 s → 15 min); 60 s lease renewed while the job runs (was a fixed 6-minute lease); `error` cleared on success. | `proxy/enterprise_workflows.py` |
| Database outage reported as HTTP 401 | Identity check failures caused by an unreachable database return 503 + `Retry-After`; lost database connections inside handlers also return 503. Invalid proofs still return 401. | `proxy/access_policy.py`, `proxy/main.py` |
| Model health invisible | `/health/ready` reports `models.embedding` / `models.generation`; gauge `gcor_model_available{role}`. Readiness itself is unchanged (retrieval degrades to keywords without models). | `proxy/main.py` |
| Not self-contained by default | Base Compose defaults to local Ollama `nomic-embed-text` (768-d) instead of OpenAI; Ollama image pinned to the digest in `docker-compose.production.lock.yml` (override with `OLLAMA_IMAGE`); `.env.example` no longer carries an OpenAI placeholder key. | `docker-compose.yml`, `docker-compose.offline-verification.yml`, `.env.example` |
| CI red since 11 Sep | Compose validation placeholder for `AUDIT_PACK_SIGNING_KEY`; `smoke.py` expectation fixed (a deduplicated re-ingest reuses the original record); Python locks refreshed for certifi, mcp 1.30.0, PyJWT, pypdf 6.21.2, urllib3 2.8.0; `@modelcontextprotocol/sdk` 1.32.1 with a rebuilt agent bundle; gitleaks given the token it needs for PRs; the integration stack's MinIO (no longer pullable from quay.io) replaced in CI by a moto S3 server built from PyPI; build/start and LAN-test failures now surface as annotations. | `.github/workflows/ci.yml`, `tests/integration/smoke.py`, `tests/integration/s3-mock.Dockerfile`, `docker-compose.integration.yml`, `*/requirements.lock`, `agent-tools/gcor-agent/*` |

## Before and after (replica)

| Measure | Before (`f2c789c`) | After |
| --- | --- | --- |
| Projector credential publishes approved knowledge into another channel | Yes | No: stored as unapproved, never returned to members |
| Graph-expansion query, 100k chunks, runtime role | > 60 s (did not finish) | 0.02 s |
| Default `/api/ask` (graph on), 100k chunks, 1 client: p50 / p95 | unusable (> 12 min) | 0.31 s / 0.37 s |
| Successful asks/s at 100k chunks, 4 / 16 concurrent | 3.9 / n.a. (graph off; 98% rejected at 8) | 6.4 / 6.6, 0% rejected |
| 1,440 chunks, 8 / 16 concurrent: rejected requests | 89% / 92% | 0% / 0% (28.6 asks/s) |
| Required source among the 4 citations, 1,440 chunks | 97.6% | 99.6% |
| Same, 100k chunks with approved near-duplicate noise (substitute embedding) | 30.1% | 76.0% |
| Same, embedding model down (keyword fallback), 1,440 chunks | 53.7% | 100% |
| No-answer questions correctly abstaining (6) | 0 | 6 |
| Cross-channel / cross-tenant chunks returned (294 questions) | 0 | 0 |
| Citations failing hash / state / channel verification | 0 of 1,176 | 0 of 1,152 |
| 150 s object-store outage during ingestion | 6/10 jobs failed permanently | 10/10 completed automatically, 60 s after restore |
| 150 s embedding outage during ingestion | 10/10 failed permanently | 10/10 completed automatically, 75 s after restore |
| Orphaned job after worker SIGKILL | resumed after 360 s | resumed after 61 s; no duplicates |
| 5 s database outage under load | 49 × HTTP 401 | 51 × HTTP 503, first success 0.2 s after recovery |

The 100k-chunk recall depends on the embedding model. Under the substitute model, vector weight 0.35
gave 88% and 0.75 gave 32%; re-tune `RETRIEVAL_VECTOR_WEIGHT` and `RETRIEVAL_MIN_VECTOR_SCORE` with
`scripts/evaluate-knowledge.py` against `nomic-embed-text` on the lab before relying on either value.

Verification on this branch: 181 proxy unit tests plus the projector, relationship-projector,
recovery-controller, graphiti-projector and MCP suites pass; integration scripts smoke, rls_scope,
enterprise_boundaries, nostr_membership, workspace_lifecycle, buzz_chat_lifecycle, governance_faults,
governance_outage, relationship_rls, attachment_lifecycle and migration_runner pass; all 11 Compose
combinations in CI render; Node agent and desktop-card tests pass; `pip-audit` and `npm audit` report
no known vulnerabilities in the locked dependencies. The relay lifecycle test needs the relay image
and was not run.

CI on this branch: every job passes, including the full integration lifecycle and the real Buzz
relay knowledge loop, except `tests/test_lan_acl_behavior.ps1` on the Windows runner (ACL rollback
SDDL comparison in `scripts/protect-gbuzz-secrets.ps1`). That test covers LAN-exposure hardening,
which this deployment does not use, and needs a Windows host to debug.

## Migration 0016

Additive and backward-compatible: older GCOR images ignore the new column and indexes, so an image
rollback needs no database rollback. `ADD COLUMN ... GENERATED ... STORED` rewrites `gcor.documents`
once (about 1 s per 100k documents on the replica) under an exclusive lock; run it in a quiet window.

## Rolling out on the lab server

1. Take the usual pre-release encrypted backup (`scripts/backup-stack.ps1`).
2. Check out this branch in `C:\Gbuzz` and build the knowledge image:
   `docker build -f proxy/Dockerfile -t gbuzz-knowledge-durable:blockers-20261022 .`
3. Apply migrations with the running startup plan's Compose files:
   `docker compose <startup plan -f files> run --rm --no-deps gcor-migrate`.
4. Set `GCOR_TRUST_IMAGE=gbuzz-knowledge-durable:blockers-20261022` in `.env` and recreate only
   `gcor-proxy`, `gcor-enterprise` and `buzz-knowledge` with `--no-deps`.
5. Confirm `/health/ready` shows `models.embedding: ok`, then run an owner-reviewed evaluation
   with `scripts/evaluate-knowledge.py` to calibrate the two retrieval settings.
6. Re-install the agent tools so agents pick up the rebuilt `gcor-agent` bundle.

Rollback: set `GCOR_TRUST_IMAGE` back to `gbuzz-knowledge-durable:knowledge-loop-20261010` and
recreate the same three services.

## Still open (needs the lab host or an owner decision)

- Calibrate retrieval weights and the abstention floor with the real embedding model, and measure
  `qwen2.5` generation latency, faithfulness, abstention and prompt-injection behaviour.
- Prove offline operation: build and run on the lab with outbound network blocked.
- Free disk on C: (8.4% free observed; the capacity gate requires 20%).
- **MinIO image availability.** `quay.io/minio/minio@sha256:14cea…` now returns `unauthorized`, and
  Docker Hub images were withdrawn earlier. The lab runs from its locally cached copy, so a rebuilt
  host could not obtain its object store. Export every runtime image to the backup set
  (`docker save`) or choose an S3-compatible store with a maintained public image.
- Off-host backups and human alert delivery remain recommended, but are outside this release's scope.
