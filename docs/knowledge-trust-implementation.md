# Trustworthy and durable knowledge loop

This release keeps Buzz as the human/agent workspace and PostgreSQL as authoritative storage. Graph remains optional. It adds a local trust deployment overlay, durable discussion synthesis, full proposal pagination, post-inference evidence checks, and a quality-review gate.

## Human workflow

- `!knowledge synthesize` selects the latest 50 original discussion messages, or the original message to which the command replies.
- `!knowledge synthesize EVENT_ID ...` explicitly selects up to 200 distinct original messages.
- `!knowledge synthesize session START_EVENT_ID END_EVENT_ID` selects the inclusive chronological range. Equal timestamps are ordered by event ID. Use explicit IDs if those tie-breaking boundaries do not match the intended selection.
- `!knowledge status COMMAND_EVENT_ID` shows job state and checkpointed batches.
- `!knowledge cancel COMMAND_EVENT_ID` cancels unfinished work. The requester or a human channel administrator may cancel; an agent cannot cancel another author's work using an administrator role.
- `!knowledge show DOCUMENT_ID PAGE` reads a complete proposal three chunks at a time and gives the next-page command.
- `!knowledge approve DOCUMENT_ID REVISION | review rationale` records the human decision. A multi-section synthesis requires a rationale. The reviewer must inspect every page and resolve cross-section conflicts; software cannot prove that someone read or understood the content.

Jobs preserve signed original event identities, authors, timestamps, content, a snapshot hash, generation model, prompt version and completed sections. They process six excerpts per batch, splitting long contributions to avoid the answer generator's excerpt truncation. Each batch is saved before yielding to other work. The existing single-worker advisory lock prevents concurrent command execution. Replayed command IDs reuse the job. Restarts may repeat an inference call interrupted before checkpoint commit, but cannot publish duplicate documents for that command.

Answers recheck their evidence after inference. Persisted chat answers also recheck source revisions and permissions before delivery retries; stale answers are suppressed. The HTTP client signs each retry with a fresh nonce while preserving the logical request ID, so transport replay prevention does not break operation idempotency.

HTTP and chat share the same live identity/sponsorship check. Workspace approval and submission require a human identity, even when an agent carries an administrator role label. Sponsored agents contribute through the chat command policy. Response release checks current role, agent classification, access level and sponsorship again.

Admission is bounded to three unfinished jobs per channel, 200 messages, 120,000 input characters and 40 inference batches per request. An oversized explicit session fails rather than silently truncating. Default selection is deliberately the latest 50 messages, not an entire channel history. Sources and sponsor authorization are checked again on resume and after inference. Model/prompt changes invalidate unfinished runs. Every section cites original event IDs; generated sections are not recursively treated as supporting evidence. Cross-section contradiction resolution remains a human task.

## Access and deployment

Apply `docker-compose.knowledge-trust.yml` after enterprise/Buzz overlays. It requires a separate database login and starts the three knowledge services and event projector as a non-root OS user. Knowledge APIs refuse privileged/owner database identities at startup. Legacy unscoped content access is disabled; the event projector has a dedicated ingestion workload token. The broad legacy MCP listener is no longer published to the host; its legacy content tools are intentionally not granted an enterprise user identity. Buzz's signed workflow is the supported employee boundary.

Migration 0015 closes unscoped chunk access and scopes session entries, participants and synthesis jobs. Background invalidation and monitoring use explicit channel/workload contexts. Application identity checks still apply; RLS is defense in depth, not protection against a fully compromised application holding trusted workload credentials.

The recovery controller communicates with a dedicated socket boundary. That boundary permits only sanitized inventory and restarts of containers labeled for this stack and restart action. It refuses create, exec, archive, image and volume operations. The controller itself has no Docker socket mount.

The canonical startup plan includes the trust overlay, knowledge worker, enterprise API, socket boundary, and the already-active endpoint monitoring overlay. Automatic image refresh refuses to replace a trust release. Production preflight and image checks include enterprise/Buzz/trust overlays. The local trust release is not a registry-signed production release.

`scripts/verify-knowledge-runtime.py` checks each runtime role, unscoped reads, image identity, health, migration hashes, and absence of the controller socket mount. It prints no secret values. Run it after every rollout and before accepting a release. `scripts/deploy-knowledge-trust.py` retains previous images and applies only application/control services; database and object-store containers stay in place. An encrypted backup must precede deployment. Retained images enable operator recovery; the script does not automatically reverse schema changes.

The migration runner commits each migration and ledger entry together under a database lock. Identical recorded files are skipped; changed recorded files fail. Add a new migration to change a deployed schema. An old installation without ledger records must have its schema assessed and backup verified before replaying the existing idempotent baseline.

Migration hashes normalize CRLF to LF. A legacy raw hash is converted only when the current file exactly matches that recorded hash (or its equivalent LF/CRLF form); meaningful edits remain errors. Fault tests verify that DDL and its ledger entry both roll back on failure.

## Verification and quality gates

Use the isolated integration stack for signed chat, restricted-role, governance and recovery tests. It uses synthetic users and separate storage. The additional offline overlay uses cached local models read-only, a private internal Docker network, separate PostgreSQL/MinIO storage and no published API port:

```powershell
docker compose -p gbuzz-offline-check -f docker-compose.integration.yml -f docker-compose.offline-verification.yml up -d --wait gcor-proxy
docker compose -p gbuzz-offline-check -f docker-compose.integration.yml -f docker-compose.offline-verification.yml run --rm --no-deps smoke
```

This exercises application ingestion/retrieval with local inference and blocked network egress. It does not prove offline installer completeness or clean-host disaster recovery. Cleanup must name `gbuzz-offline-check` explicitly and must not delete the external cached-model volume.

The evaluation report now records a hash of each exact answer response. `scripts/check-knowledge-quality.py` requires at least 100 distinct evaluated cases, successful automated checks, owner reviews of matching hashes, acceptance of each case and at least 95% supported factual claims. Supply a JSON review array with `id`, `response_sha256`, `reviewer`, `reviewed_at`, `factual_claims`, `supported_claims`, and boolean `acceptable`; explicitly name permitted owners using `--owner`. This local ledger is supplied operating evidence, not cryptographic reviewer authentication. Retain the answers and approve the ledger through organizational governance.

## Remaining organizational acceptance

Off-host backup destination, independent recovery-key custody, named alert recipients/destination, retention/deletion rules and recovery objectives need organization-specific decisions. Existing scripts/runbooks provide the backup and alert activation paths. No destination or recipient is invented by this release. Complete a clean-host recovery drill and delivered/acknowledged alert test after configuration.

The 100-case corpus and semantic reviews must come from knowledge owners. Complete a real human native-Buzz review session, load/capacity measurements, signed release distribution and a reviewed deletion policy before claiming enterprise production readiness. This release does not automatically rewrite or approve organizational knowledge.
