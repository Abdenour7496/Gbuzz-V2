# Enterprise knowledge workflows

Deployment record: 8 September 2026. The local stack now supports a governed knowledge pilot using Buzz/Nostr identities. This is not certification for unrestricted enterprise production use.

The target user interface is Buzz itself, including knowledge creation and management. The standalone workspace below is an interim development/review tool; see [the confirmed interface objective](collaborative-knowledge-objective.md).

## Available now

- Workspace: http://127.0.0.1:5011/workspace. Connect an existing NIP-07 signer; keys stay in the signing facility. A host can also use `clients/buzz-knowledge.mjs`. Buzz Desktop itself is unchanged.
- Active channel members can read approved knowledge. Contributors (member/admin/owner) can submit text proposals; owners/admins can review, assign an owner and review date, and change document reader lists. Guest/bot roles cannot contribute or approve. There is no implicit administrator membership bypass.
- Document reader lists narrow channel access across the workspace, retrieval and graph traversal. An absent list inherits channel access. Reviewers and assigned owners remain included when an explicit list is set. The private service API remains a privileged internal surface.
- Review changes use optimistic concurrency and transactional governance publication. Feedback and verified actor history are visible in document details.
- Durable ingestion jobs support status, retry and cancellation before processing. Workers recheck live membership before ingestion. Leases, bounded attempts and fenced acknowledgements recover interrupted work. Delivery is at least once: duplicate processing may produce additional recovery artifacts, but existing document governance is preserved. New submissions are limited when an actor already has 20 pending/processing jobs.
- PDF text, DOCX and PPTX extraction retain source anchors. Scanned PDFs require OCR and are quarantined; unsupported binaries are rejected rather than indexed as corrupted text. The workspace currently submits text, not file uploads.
- Optional Graphiti retrieval returns authorized source-document candidates, with permissions checked again in SQL. Unknown or unauthorized provenance is discarded; raw graph inferences are not inserted into answers. A three-second deadline falls back to document retrieval. `GRAPHITI_RETRIEVAL_ENABLED=true` enables this on the enterprise service. Embedding and generation fallback paths remain available.
- Projector heartbeat checks, ingestion queue/worker metrics and alerts are active. Prometheus, Grafana and the local Alertmanager receiver are deployed. External alert delivery is not configured.

## Operation

Apply migration `0008_enterprise_workflows.sql` and all previous migrations before starting the workflow services. Only the private core runs ingestion workers. The enterprise service handles signed requests and shares the database.

For this deployment, include `docker-compose.yml`, `docker-compose.safeguards.override.json`, `docker-compose.enterprise.yml` and `docker-compose.observability.yml` with the `observability` profile when inspecting the complete stack. Preserve the managed core image override. Do not remove optional services as orphans. The guarded core deployment script retains a rollback image; database migrations are additive and should not be reversed casually.

Set `GCOR_PUBLIC_ORIGIN` to the exact signed origin when deploying behind TLS. Keep private GCOR/MCP, database, object store, Graphiti and recovery endpoints behind operational access controls. See the production deploy checklist for image pins and release gates.

Run `pwsh -NoProfile -File scripts/restore-drill.ps1` for an isolated PostgreSQL and referenced GCOR object restore. It does not stop the live stack. It retains a database snapshot, copied objects and checksums under `backups/restore-drill-*`. Backups contain application data and require protected storage. This drill excludes unrelated relay media, graph rebuild and host loss.

`scripts/evaluate-knowledge.py` accepts a JSONL corpus and `--output report.json`, with optional `--concurrency` and `--max-p95-seconds`. It uses `STACK_API_SECRET` from the environment against a private staging API. Each case includes `id`, `query`, `channel_id`, `access_level`, and optional `required_document_ids`, `forbidden_document_ids`, `forbidden_strings`, `expect_no_answer` and `min_recall`. It checks source recall, reference validity, forbidden content and latency; human reviewers must still assess whether claims are supported.

## Verification evidence

- 70 unit/helper tests passed: 59 proxy, two signing helper and nine projector tests.
- Real isolated database integration passed ingestion/retrieval smoke, signed membership/revocation, permission boundaries and the workspace proposal-to-review lifecycle. Seven governance fault tests passed.
- A two-case deterministic evaluation smoke passed. Its latency is not a production performance benchmark.
- Restore drill `backups/restore-drill-069258f29a6f4372bd5e98805b58c5f4/report.json` passed: three documents, three chunks and nine referenced objects verified. The database snapshot preceded migration 0008.
- Twenty long-running services were running after deployment. All 13 application services reported Docker healthy. The monitoring receiver was healthy; the other six monitoring containers lack Docker health checks, with seven Prometheus targets UP and Grafana responding. A synthetic alert reached the local receiver. Eighteen alert rules passed validation.
- Workspace visual inspection passed. An actual employee signing session remains untested; synthetic identities were used for authenticated integration tests.

## Remaining production gates

1. The first internal collaboration loop is now [available directly in Buzz](buzz-chat-knowledge.md). Expand the command-based pilot with rich client interactions, continuous synthesis and task delegation. External business sources remain out of scope.
2. Supply an off-host backup destination and recovery objectives; test complete host-loss recovery, relay media and graph rebuild. The local restore drill cannot establish these guarantees.
3. Supply representative questions and knowledge-owner judgments for answer quality and realistic concurrent-load evaluation.
4. Define retention, legal hold and purge rules before implementing destructive lifecycle automation.
5. Complete deployment-specific TLS, secret management, immutable release pins, vulnerability review, external alert routing and high-availability design. Database row-level security remains a defense-in-depth improvement; current enforcement is in the application.
6. Scope agent tools and connect the Buzz conversation experience to the review workspace. OCR/XLSX and external connectors are not current priorities.
