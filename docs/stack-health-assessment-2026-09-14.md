# Stack health and enterprise knowledge readiness — 14 September 2026

## Assessment

The stack is operational and has a credible collaborative knowledge foundation. It is suitable for continued controlled pilot development, but the live deployment does not yet demonstrate readiness for wider confidential enterprise use. Keep Buzz as the collaboration interface, PostgreSQL as authoritative knowledge storage, MinIO for artifacts, and local inference. Prioritize deployment trust and recovery, then improve the continuous knowledge lifecycle.

Scope: live container status, selected non-secret runtime settings, database role and policy flags, HTTP health, Prometheus targets/alerts, resource snapshot, repository implementation and earlier evidence. No deployments, production records, secrets or existing changes were modified. This is not a load test, penetration test, full integration run or offline-operation certification.

## Fresh operational evidence

| Check | Result |
|---|---|
| Containers | All 18 listed containers running; every container with a reported health check healthy |
| HTTP | Relay readiness ready; core and enterprise GCOR health ok |
| Monitoring | All eight scrape targets up; no active alerts returned |
| Restarts | Knowledge worker has one restart; core API, enterprise API and recovery controller have zero. Cause not investigated |
| Resources | Low CPU at sampling; no obvious container memory pressure. This was an idle snapshot, not inference capacity evidence |
| Host disk | Approximately 41.7 GiB free, 17.5% of the C: drive; limited margin above the preflight's 15% minimum |
| Inference | Local qwen2.5:7b, qwen2.5:1.5b and nomic-embed-text installed; knowledge services configured for qwen2.5:1.5b generation and Ollama embeddings |
| Graph | No graph services running; consistent with the documented optional design |

## Highest-priority findings

1. **Live database privileges bypass the intended defense in depth.** All three inspected knowledge services have `POSTGRES_USER=buzz`; all 15 database sessions observed use `buzz`. That role is both superuser and BYPASSRLS. Documents, chunks and nodes have RLS enabled, but it cannot constrain this role. A non-superuser `gcor_app` role exists, but its presence does not establish application use. Deploy and verify the intended restricted runtime role, retain a separate migration identity, and run signed identity/revocation and direct-access integration tests before expanding access. Application-level authorization may still operate; this finding does not establish an actual disclosure. PostgreSQL documents the bypass behavior in its [row-security guidance](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).

2. **Recovery controller retains the Docker socket.** The running container mounts `/var/run/docker.sock`; no socket-proxy container appears in the live inventory. Introduce the reviewed restricted control boundary and verify both allowed recovery actions and rejected operations. Docker daemon access is a privileged boundary: see [Docker security](https://docs.docker.com/engine/security/).

3. **Release and migration evidence need reconciliation.** Services run a mixture of local image names, including `gbuzz-knowledge-wiki:20260908`. The live migration tracking table contains zero rows despite schema features being installed. This is an evidence gap, not proof that migrations are absent: compare installed schema and migration history before applying anything. CI now includes enterprise/Buzz and full Compose combinations, improving on the older assessment. However, the preflight's rendered configuration omits the enterprise/Buzz overlays, and the saved startup configuration omits production overlays. Establish one explicit release manifest, check the actual deployed images and service settings against it, and test rollback. Existing uncommitted backup/observability work must be preserved and included deliberately in a release.

4. **Recovery remains tied to the same host.** Today's backup report records authenticated encrypted backup verification and a successful isolated sample restore. It explicitly states that off-host backup storage and independent recovery-key escrow are not configured. Add an organization-controlled independent destination and recoverable key custody, then demonstrate a clean-host restore. A six-hour schedule is not itself a verified recovery-point guarantee.

5. **Monitoring does not yet establish human response.** Targets are healthy, but the alert runbook says only a local audit receiver is deployed. Agree operator ownership and a destination, then verify firing, delivery, acknowledgement, escalation and resolution end to end. A working dashboard alone cannot provide this evidence.

## Improvements toward a self-contained knowledge brain

| Order | Deliverable | Acceptance evidence |
|---|---|---|
| 1 | Deploy and verify runtime least privilege, restricted Docker control and a reproducible release | Real restricted-role tests pass; unauthorized channel, document and agent access rejected; release matches reviewed manifest |
| 2 | Complete host-loss recovery and human incident response | Clean-host restoration within agreed RTO; measured RPO; independent key recovery; delivered and acknowledged test alert |
| 3 | Replace the six-message synthesis window with durable session-based jobs | Explicit session revision and supporting entry IDs; chunked processing for long conversations; retries, deduplication, cancellation, per-channel budgets; restart and source-edit tests |
| 4 | Close the learning cycle in Buzz | Discussion → proposal → human review → cited reuse → correction → supersession demonstrated in the real native interface; owners and review deadlines visible |
| 5 | Measure knowledge quality | At least 100 owner-reviewed cases covering decisions, contradictions, corrections, absent evidence, malicious instructions and restricted channels; score semantic support as well as citation validity |
| 6 | Prove self-contained operation | With outbound access disabled in an isolated environment, demonstrate capture, local inference, review, retrieval, restart and restore; retain required images, models, configuration and install artifacts |
| 7 | Measure scale before changing architecture | Concurrent end-to-end workload with answer p95, synthesis backlog, ingestion lag, resource peaks, and recovery behavior under dependency failure |

Current synthesis SQL reads at most six preceding entries. Living-knowledge documentation caps maintenance at 200 pages and its AI pass at ten pages. These are appropriate bounded pilot behaviors, but they cannot demonstrate comprehensive enterprise memory. Prefer durable incremental synthesis with explicit evidence and unresolved conflicts; preserve mandatory human approval and prevent model summaries from becoming independent evidence.

The evaluation script already checks retrieval recall, forbidden content and citation numbering, but explicitly leaves semantic claim support to owner review. Benchmark the installed 1.5B and 7B models on the same approved test corpus before choosing a default; model size alone cannot establish quality or acceptable latency.

Define retention, deletion propagation, classification, ownership, key recovery and agent sponsor revocation as operating rules. Keep external business connectors outside the current objective. Add graph complexity only if measured authorized retrieval improves.

## Verification limits

The selected local Python test run reported 25 test entries with four errors: 21 completed successfully, while three modules could not import and one test failed to initialize because `coincurve` is missing from `.venv-review`. This is an incomplete test run, not evidence of four product defects. Rebuild the isolated test environment from the pinned dependencies and run the full relevant integration gates before deployment. No package installation or live test writes were performed.

Backup restore and native-client workflow statements above distinguish earlier recorded evidence from checks performed in this assessment. No clean-host restore, full native human review journey, vulnerability scan, inference load test or network isolation test was performed today as part of this assessment.
