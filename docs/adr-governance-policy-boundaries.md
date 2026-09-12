# ADR: Governance policy boundaries

Status: accepted design baseline. This package does not activate a policy or delete records.

## Configurable owner choices

- Classification names beyond the safe built-in ordering and who may downgrade them.
- Record categories, retention/review/export periods, date triggers and jurisdiction/business mappings.
- Named owners, reviewers, records administrators, hold custodians and export approvers.
- Review and propagation SLAs; overdue behavior by category.
- Legal-hold authority/reference format and initial need for dynamic query/custodian scope.
- Audit Pack recipients, single/dual approval threshold, delivery, expiry and retention.
- Tenant-offboarding outcomes and the authority that certifies completion.
- Explicit, documented exceptions for offline or immutable copies outside immediate control.

Until those choices are made, the default template classifies as `restricted`, quarantines unclassified records, requires owner selection for all durations and authorities, excludes overdue knowledge from authoritative use, prohibits automatic deletion and requires dual-human Audit Pack approval.

## Non-negotiable integrity controls

- `archived` means retired from active use, never deleted.
- Original evidence is immutable; corrections and lifecycle changes append history.
- Derived data is rebuildable and never becomes authority independently of its sources.
- Human approval is required for authoritative knowledge, classification downgrade, hold release, disposition, controlled export release and tenant offboarding; an agent cannot satisfy that authority.
- Active legal hold blocks every disposition route, including storage lifecycle, bulk administration, tenant offboarding and restore.
- The strictest source access/classification/retention constraint governs a derivative.
- Every approval binds the exact content revision, evidence set, policy version and reviewer delegation.
- Disposition is two-stage, idempotent, reconciled across every store/version and completed only by a signed certificate with explicit exceptions.
- Restore cannot serve or rebuild data until current holds and completed dispositions are replayed.
- Audit Pack release is recipient-, purpose-, scope- and expiry-bound, logged, independently verifiable and governed as a new record.
- Authorization and lifecycle controls fail closed when policy, identity, evidence, lineage or custody is missing or unverifiable.
- Production certification is derived only from assertion-level test artifacts bound to the exact commit, environment, policy/schema versions and required human decision; editable capability labels are never authority.

## Consequence

Configurability can adapt the platform to an owner's obligations, but configuration by itself is not evidence of legal compliance. Owners remain accountable for obtaining appropriate legal/records advice and approving the schedules and authorities they activate.
