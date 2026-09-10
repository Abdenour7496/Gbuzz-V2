# Production readiness review — 7 September 2026

Follow-up: roadmap items 2 and 3 were implemented on 8 September. See
[transactional governance and outbound fetch restrictions](governance-and-egress.md)
for migration requirements, behavior, and verification.

## Assessment

Gbuzz has a useful operational baseline: authoritative PostgreSQL data, MinIO recovery records, rebuildable Graphiti projections, dependency locks, CI integration tests, deployment overlays, monitoring, and a bounded recovery controller. It is suitable for further private staging validation. This review does not establish readiness for public or untrusted multi-user access.

Reviewed the proxy ingestion/retrieval and governance paths, MCP bridge, event and Graphiti projectors, recovery controller, CI, integration stack, and production checklist. Existing uncommitted work was preserved. No live deployment or stored knowledge was changed.

## Fixes delivered

| Issue | Change | Verification |
| --- | --- | --- |
| Attachment redirects bypassed URL restrictions | Both download entry points now use a shared streaming downloader that checks policy before every redirect request and limits redirect chains to five hops. | Tests cover an allowlisted host redirecting to a blocked host, relative redirects, and redirect loops. |
| Remote bodies were fully buffered before size validation | Read in bounded chunks and reject oversized content before appending the chunk that exceeds the configured limit. Response streams close on failure. | A generated large stream is stopped after two chunks; retry behavior also passes. |
| Recovery CI job could not import `main` | Set the component import path, matching the other component jobs. | Reproduced the import failure, then passed all six recovery tests with the corrected environment. |
| Local test environments could enter build/release inputs | Exclude `.venv*/` from Git and Docker contexts. | Review environment is ignored and integration image build succeeds. |

## Prioritized roadmap

### P0 — before untrusted users or wider network exposure

1. **Identity-based permissions and scoped MCP credentials.** The HTTP request models and MCP tools accept caller-selected `access_level`, `agent_id`, and governance actor fields. A shared secret proves possession, not channel membership or approval authority. Derive identity and scope at an authenticated boundary; enforce channel membership and separate reader, contributor, approver, and recovery permissions. Acceptance: one user cannot retrieve, inspect sessions, approve, or recover another user's private content, including through MCP and graph expansion.
2. **Transactional governance events.** Approval updates PostgreSQL, queues graph work, then writes the MinIO governance artifact in separate steps (`approve_knowledge`; similar lifecycle paths). A failure can leave partial durable state and an ambiguous client error. Write governance state and an outbox event in one transaction, publish artifacts asynchronously, and make replay idempotent. Acceptance: injected database, graph, and object-store failures eventually converge without losing or duplicating a transition.
3. **Enforced outbound network boundaries.** The redirect fix closes one URL-policy bypass, but DNS validation and connection establishment remain separate resolutions. Use restricted outbound networking or a fetch service that connects only to validated addresses; deny metadata and internal services at the network layer. Acceptance: DNS rebinding and redirect chains cannot reach protected destinations.
4. **Total request and work limits.** Uploaded files still use an unbounded `await file.read()` before downstream validation, while text/session inputs can trigger substantial parsing and embedding work. Add ingress body limits, bounded upload reads, total attachment-byte and processing-time budgets, and per-identity concurrency/rate limits. Acceptance: oversized multipart, text, compressed remote responses, and slow streams are rejected within measured memory and time budgets.

### P1 — reliability and operator features

5. **Ingestion job status and retry controls.** Add durable jobs with idempotency keys, progress, cancellation, error categories, and controlled retry/dead-letter replay. Expose them through MCP and a small operator view. Acceptance: clients can recover from disconnects without submitting duplicate work, and operators can resolve a failed attachment without replaying an entire session.
6. **Dependency readiness and service objectives.** `/health` currently checks PostgreSQL only. Separate process liveness from bounded readiness checks for required dependencies; report degraded inference/object storage distinctly. Add saturation, queue lag, disk, latency, and error-budget dashboards with tested alert delivery. Acceptance: simulate each dependency outage and verify the expected readiness and notification behavior.
7. **Automated restore drills.** Extend existing backup/release tooling with isolated restore validation, checksums, record counts, provenance sampling, and measured RPO/RTO. Acceptance: a scheduled drill restores PostgreSQL and MinIO together and rebuilds Graphiti from authoritative records with retained evidence.

### P2 — product quality

8. **Knowledge review workspace.** Show proposed knowledge, source evidence, conflicting corrections, approval history, and superseded versions with role-restricted actions.
9. **Retrieval evaluation suite.** Maintain representative questions with expected citations and forbidden cross-channel results. Track answer grounding, retrieval recall, latency, and inference cost before changing models or chunking.
10. **Retention and deletion workflow.** Define how a deletion propagates through PostgreSQL, versioned MinIO objects, projections, backups, and audit records, including retention exceptions. Provide inspectable progress and reconciliation evidence.

## Validation evidence and limits

- 27 unit tests passed: proxy 12 (including five new download regressions), event projector 2, Graphiti projector 7, recovery controller 6.
- Docker integration build and lifecycle smoke test passed with exit code 0 under the isolated project `gbuzz-review-20260907`. Its containers, network, and test volumes were removed afterward.
- Local tests used locked Python packages in an isolated Windows environment. Linux-only `uvloop` was omitted from temporary lock copies; Windows MCP support packages were added locally. Repository dependency locks were not changed.
- The integration test uses deterministic mock inference; it does not validate actual model output, Graphiti end-to-end reconciliation, production load, restore objectives, or public ingress security.
- No new vulnerability scan, external penetration test, or production deployment approval was performed. The existing production deployment checklist still applies.
