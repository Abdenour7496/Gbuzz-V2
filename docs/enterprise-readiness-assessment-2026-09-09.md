# Enterprise readiness assessment — 9 September 2026

## Decision and scope

Retain the core stack. It is a credible foundation for a controlled internal pilot; the reviewed evidence does not establish enterprise production readiness. The next investment should be authorization, evidence integrity, release discipline and recovery, followed by continuous knowledge creation and measured scaling.

The objective is people and agents collaborating inside Buzz to produce reviewed, reusable, evolving knowledge. Buzz remains the interface and Nostr the identity authority. External connectors and a replacement identity provider are outside this recommendation. See [confirmed objective](collaborative-knowledge-objective.md).

This assessment inspected the current working tree, including uncommitted implementation, Compose definitions, CI, application code and recorded verification. It did not change application code or deployments, run tests, inspect current live service health, perform a vulnerability scan or measure capacity. Earlier documented test and deployment results are historical evidence, not fresh verification. User count, hosting constraints, availability requirements and budget remain unspecified. Assume one organization with multiple channel permission domains for initial planning.

## Stack decisions

| Component | Recommendation | Reason and next improvement |
| --- | --- | --- |
| Buzz/Nostr and desktop knowledge cards | Keep | Fits the collaboration objective. Exercise real human review journeys, establish key recovery/rotation and deactivation procedures, and sign desktop releases. |
| Python/FastAPI GCOR | Keep; improve internal structure | Existing APIs, governance and workers cover the core use case. Separate authorization, retrieval, ingestion and lifecycle interfaces within the codebase; `proxy/main.py` is approximately 2,900 lines. Avoid multiplying deployable services without measured need. |
| PostgreSQL 17/pgvector | Keep as authoritative store | Transactions, provenance, hybrid search and durable jobs fit well. Separate migration and runtime roles, add database policy enforcement and prove backup/failover behavior. |
| MinIO | Keep provisionally as object store | Recovery artifacts are valuable. Validate the selected distribution's maintenance, licensing and support separately; establish scoped credentials, protected off-host copies and lifecycle rules. |
| Graphiti/FalkorDB | Keep as optional derived capability | Authoritative records survive graph loss. Measure improvement over document retrieval and extraction backlog before increasing investment or concurrency. |
| Ollama/model inference | Keep behind explicit configuration | Benchmark real workloads and models. Separate deployment hardening from the decision to send content to external inference. |
| Redis | Keep for existing Buzz needs | Do not introduce another queue system merely for enterprise branding; PostgreSQL jobs already provide leases and retries. |
| Compose and monitoring | Keep for isolated pilot | Move to redundant hosting when agreed availability requires it. Monitoring needs delivered alerts and operating ownership, not just running containers. |

## What is already implemented

The older assessments are partly superseded. Current code and [workflow evidence](enterprise-workflows.md) include signed Nostr request validation, live membership checks, document reader restrictions, sponsored agents in the Buzz command path and human-only approval. Governance uses transactions and an outbox; ingestion jobs have leases, retries and bounded outstanding work.

The answer path now uses the same bounded evidence set for generation and citations, rejects invalid citation numbers and supports lexical/excerpt fallbacks. Optional Graphiti retrieval resolves candidates back to authorized database documents. These fixes should be preserved rather than reimplemented.

[Living knowledge](buzz-living-knowledge.md) adds revision review, atomic supersession, saved answers, history and requested maintenance. CI defines unit, real database, relay, revocation and fault tests; image publication includes SBOM/provenance settings. These are meaningful foundations, but current local changes must become reproducible release inputs.

## Prioritized improvements and acceptance gates

### P0 — before wider confidential use

1. **Reduce infrastructure privileges and close alternate access paths.** *Largely implemented 10 September: migration 0011 and `GCOR_DB_USER` give every runtime service a non-owner, non-superuser, non-BYPASSRLS role; migration 0012 plus `proxy/db_scope.py` add channel/access-level RLS on documents, chunks and nodes bound to the signed identity; the CI lifecycle suite runs as that role and `rls_scope.py` proves the policies. Same day: scoped MinIO user/policy (`GCOR_S3_*`, `GCOR_CHANNEL_BUCKET_PREFIX`), internal `data-net`/`control-net`, and a filtering `docker-socket-proxy` replacing the controller's socket mount — Compose-validated, live verification pending.* Base Compose supplies the same PostgreSQL user to initialization and application services; migrations contain no row-level-security policy setup. Private legacy HTTP/MCP remains broadly privileged. Introduce distinct migration, API, worker and recovery roles, bucket-scoped object credentials, restricted service networks and a narrowly controlled Docker socket interface. Run containers as non-root where supported. Preserve server-derived channel/actor checks and add RLS as defense in depth. Test direct APIs, MCP, graph provenance, sessions, histories and artifacts for permission bypass. Runtime roles must demonstrably lack superuser/BYPASSRLS privileges. PostgreSQL documents that owners and privileged roles can bypass RLS: [row security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).

2. **Make evidence revocation safe during worker delays.** *Implemented 10 September: migration 0010 plus read-time checks in all approved-only paths; the isolated lifecycle test now deletes a source with the worker idle and asserts the two-hop dependent answer is hidden. See [living knowledge](buzz-living-knowledge.md).* `invalidate_removed_evidence` in `proxy/buzz_chat.py` withdraws approval asynchronously in batches of 50. This leaves a freshness window; its existence alone does not prove an exploitable disclosure. Require dependency validity and source permissions at answer/read time, with durable invalidation work for eventual cleanup. Acceptance: with the worker stopped, deleting/restricting/superseding supporting evidence cannot leave a dependent answer available as current approved knowledge. Exercise inherited and multi-hop dependencies.

3. **Create one reproducible production release configuration.** There are many overlays and substantial uncommitted changes. CI's production Compose validation does not include the enterprise and Buzz overlays. Validate the exact combined deployment, with all application services hardened and immutable image references checked. Separate the current production overlay's external Graphiti inference choice into an explicit inference profile. Add image vulnerability scanning, signature verification, pinned CI actions and a signed desktop delivery path. Acceptance: a clean checkout produces the reviewed deployment, passes gates and can roll back application images with a documented compatible database migration strategy.

4. **Prove host-loss recovery and alert delivery.** The existing drill explicitly excludes unrelated relay media, graph rebuild and host loss. Add protected off-host PostgreSQL/object/relay backups, required configuration and recoverable key material; restore on a clean host and rebuild the graph. Route alerts to a real operator and test delivery. Agree RPO/RTO first; use RPO ≤24 hours and RTO ≤4 hours only as initial pilot proposals, not existing guarantees. Production targets may require continuous database recovery and redundancy.

### P1 — complete and validate the collaboration product

5. **Add durable, bounded synthesis and review jobs.** Current discussion synthesis selects up to six messages; maintenance scans at most 200 pages and its AI pass samples ten. These are pilot limits. Add revision-bound synthesis jobs, session/topic segmentation, explicit evidence references, conflict tracking, model/run provenance, cancellation, deduplication and per-channel budgets. Trigger on explicit session completion or approved channel policy. Keep human approval mandatory and prevent generated summaries from becoming independent supporting evidence. Acceptance: duplicate events, restarts, edits and sponsor revocation yield traceable proposals without duplicate publication or unauthorized approval.

6. **Evaluate factual support and actual employee workflows.** Citation numbering validation does not prove that a claim follows from its citation. Establish at least 100 owner-reviewed cases spanning decisions, corrections, conflicts, absent evidence, prompt injection and restricted channels. Measure supported claims, retrieval recall, abstention, correction rates and proposal usefulness. Proposed initial goals: all references resolve; ≥95% supported factual claims on the reviewed corpus; zero unauthorized disclosures in the defined adversarial suite. A finite suite cannot establish universal safety. Include malicious conversation content and excessive agent authority, following [OWASP guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html). Exercise a complete native Buzz human proposal/review/revision journey; the recorded local release notes say this remains outstanding.

7. **Define lifecycle and identity operations.** Establish knowledge owners, review deadlines, classification, retention, deletion exceptions and key/device recovery. Implement inspectable deletion propagation across records, object versions, graph and backup expiry only after rules are agreed. Distinguish removing channel access from removing already-copied content. Gate agent actions on current sponsor authority, with explicit scopes and revocation coverage for every tool path.

### P2 — scale from measurements

8. **Benchmark concurrent collaboration, not just search.** Measure ingestion latency, synthesis wait, answer p95/p99, invalidation age, graph backlog, model memory and per-session inference consumption. Add pagination beyond the current catalog limits, fair work scheduling and quotas. Preserve the Graphiti single-in-flight safety bound until measured inference capacity justifies partitioned concurrency. Test slow models and dependency outages as well as normal operation.

9. **Choose availability architecture from an agreed service objective.** Compose is supported for single-server production use ([Docker guidance](https://docs.docker.com/compose/how-tos/production/)), but the current host remains a failure domain. For business-critical availability, use redundant API/workers and resilient authoritative storage with tested failover. Select orchestration based on operational capability and workload; adopting Kubernetes alone would not prove readiness.

## Suggested delivery sequence

1. Release baseline: reconcile current changes, validate the complete overlay set, lock deployment artifacts and finish a real native Buzz review session.
2. Trust milestone: runtime privilege separation, read-time dependency validation and adversarial identity/agent tests.
3. Operational milestone: clean-host recovery, external alerts, explicit lifecycle rules and measured service targets.
4. Product milestone: durable synthesis, review queues and representative knowledge-quality evaluation.
5. Expansion milestone: load evidence and a justified availability design before adding more permission domains.

Advance on evidence, not calendar dates. Knowledge owners approve quality and lifecycle rules; security/identity owners approve access boundaries; operations owns recovery and alerts; application engineering owns implementation and release evidence.

The next implementation increment should combine the production release baseline with privilege separation and dependency-validity checks. It protects the collaboration capabilities already built and provides a trustworthy base for continuous synthesis.
