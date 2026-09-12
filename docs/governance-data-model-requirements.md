# Governance data model and API requirements

This is an implementation contract, not a migration that changes a live database. All additions must be additive and preserve current reads during rollback.

## Required tables

| Table | Minimum fields and constraints |
| --- | --- |
| `governance_policies` | `policy_id`, integer `version`, immutable JSON policy, status/effective time, creator/approver, content hash; unique `(policy_id, version)`; active versions cannot be edited |
| `record_classifications` | object type/ID, tenant/channel, classification, category, policy ID/version, source/inheritance reason, classified by/at; one current assignment plus append-only history |
| `reviewer_delegations` | human pubkey, tenant/channel/category scope, authority, grantor, effective/expiry/revoked times; agents prohibited by constraint and authorization layer |
| `legal_holds` | hold ID, authority/reference, scope query snapshot and dynamic rule, custodian, state, created/approved/released by/at/reason; release distinct from creation where policy requires |
| `legal_hold_members` | hold ID, object type/ID/version, matched reason/time; immutable membership history; terminal disposition prohibited while any active hold exists |
| `lineage_edges` | child type/ID/version, parent type/ID/version/hash, derivation type, tool/model/prompt/extractor version, run ID/time, inherited policy/classification; no cross-channel edge |
| `disposition_cases` | case ID, scope, policy/version, eligibility time/result, active holds/dependencies, requester/approver/executor, reason, state, retries, reconciliation status |
| `disposition_tasks` | case ID, target store/object/version, ordered dependency, operation, idempotency key, lease/retry/error, terminal result/hash; unique target and idempotency key |
| `disposition_certificates` | case ID, signed manifest/hash, counts, exceptions, policy/version, approvals, start/end; created only after terminal reconciliation |
| `audit_export_requests` | request ID, channel/scope/time range, purpose/case, classification, recipient, requester, approver(s), expiry, state, pack ID; prohibit release before approval |
| `audit_export_access` | request/pack ID, actor/recipient, action, time, result, client/request correlation; append-only |
| `tenant_offboarding_cases` | tenant/community/channel scope, freeze time, outcome per policy, holds, export/transfer/disposition tasks, credential revocation, reconciliation and signed certificate |
| `custody_events` | subject object/version, event type, actor/workload identity, event time/receive time, source/destination, prior/event hashes, key ID, correlation ID; append-only sequence |

Every content-bearing table and lineage edge must be tenant/channel scoped and protected by RLS. Worker tables may be readable across scopes only by narrowly scoped worker roles; they must not expose content or unrestricted object URLs.

## Existing table additions

- `documents`: mandatory policy ID/version, record category, classification, owner, source authority, review status/due, hold summary, expiry/effective dates and immutable source revision/hash.
- `ingestion_records`: policy/classification snapshot, extractor/tool version, normalized-content hash, source object version, custody correlation, disposition state.
- `chunks`, `nodes`, `edges`, future claims/relationships/proposals: lineage ID, derivation run/version, inherited classification/policy, invalidated timestamp/reason. A derived object must become unreadable atomically when any source becomes unauthorized or invalid.
- `governance_outbox`: governance event type, policy version, exact before/after revision hashes, actor authority/delegation and custody sequence.
- `audit_pack_exports`: request/approval ID, recipient, classification, release/expiry/revocation/disposition state and last access; retain existing seal and object hashes.

## API surface

- `POST /api/governance/policies`, `POST /{id}/versions`, `POST /{id}/activate`: records-admin authoring and human approval; activation validates no unresolved `OWNER_SELECTION_REQUIRED` values.
- `POST /api/governance/classifications/assign`: exact object/version, reason and inheritance validation; downgrades require authorized human review.
- `POST /api/governance/holds`, `POST /{id}/approve`, `POST /{id}/release`: immutable authority/reference and scope; release never deletes records itself.
- `POST /api/governance/dispositions/evaluate`, `POST /{id}/approve`, `POST /{id}/execute`; `GET /{id}/reconciliation` and certificate download. Execution rechecks holds, policy version, authorization and dependencies immediately before each destructive task.
- `POST /api/governance/audit-exports/request`, `POST /{id}/approve`, `POST /{id}/generate`, `POST /{id}/release`; recipient-bound, purpose-bound and expiring access.
- `POST /api/governance/tenants/{id}/offboarding`, `POST /{case}/approve`, `GET /{case}/reconciliation`.
- Read APIs for policy, lineage, holds, custody, exceptions and metrics are scoped and paginated; sensitive searches require an auditable purpose.

All mutations require signed identity, current membership/delegation, idempotency key, expected revision, reason, policy version and correlation ID. Background workers receive task-specific workload identities; no shared secret alone may authorize export, hold release, disposition, or offboarding.

## Transaction and failure semantics

1. Ingestion commits evidence metadata, policy/classification and custody/outbox records atomically or quarantines without retrieval visibility.
2. Approval locks the target revision, evidence set, policy assignment and reviewer delegation. Any change returns conflict and requires fresh review.
3. Holds are evaluated at eligibility, approval, execution and restore. An active hold always wins.
4. Disposition is a durable DAG/outbox. Tasks are idempotent, leased, retryable and never mark complete until each store/version returns a verifiable result or a recorded exception is approved.
5. Restore replays holds, tombstones and completed disposition manifests before enabling reads or background rebuilds.
6. Audit Pack generation is fail-closed: no pack is returned or released unless the ledger write and approved request both succeed.
7. Offboarding first freezes ingestion/export and revokes access, then reconciles transfer, hold, retention and disposition. It cannot be represented as one bulk delete.

## Required metrics

Low-cardinality metrics: classified/unclassified totals, active holds, hold match lag, overdue reviews, eligible/pending/blocked/failed dispositions, propagation age, unresolved descendants, export requests by state, expired/unrevoked exports, custody reconciliation failures, offboarding cases by state, and restore tombstone replay failures. Object IDs, event IDs, filenames, users and free-text purposes must not be metric labels.

## Certification evidence

Ordinary CI runs `scripts/evaluate-governance-controls.py --validate` and the unit tests to validate schemas, matrices, fixtures and fail-closed behavior. This is structural assurance only and cannot certify a release.

The separate production-certification invocation requires `--evidence PATH` and derives its result from `schemas/governance-evidence.schema.json`. Every fixture assertion requires a passing test ID, readable artifact path and verified SHA-256, exact code commit, policy ID/version, governance schema version, environment profile digest and in-window run timestamp. Assertions marked as requiring human evidence also require a reviewer identity and a decision digest computed over the exact assertion result, artifact digest, commit, policy, environment and approval time. Duplicate, partial, expired, unknown, mismatched or manually asserted evidence fails closed.
