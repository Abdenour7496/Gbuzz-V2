# Knowledge trust release — 14 September 2026

The local knowledge trust changes are deployed. The final application image is `gbuzz-knowledge-durable:2b1d72b212b72da3`, image identity `sha256:e914361743584b82efcc6b967f2ff99a833d1868393f682de183f3d9899b676d`.

## Verified live state

- Core API, enterprise API, knowledge worker and event projector are healthy, run as OS user `10001:10001`, and connect as `gcor_runtime`.
- Their database role is not a superuser, cannot bypass RLS, cannot create roles and does not own the knowledge schema. Unscoped document and chunk reads return zero rows.
- All 15 migration records match the repository with normalized line endings.
- The controller has no Docker socket mount. Its separate boundary is healthy and rejects image listing and container creation. Restart permission is limited by stack/recovery labels.
- All eight Prometheus targets were up and no active alerts were returned after rollout. The synthesis-stall rule is loaded.
- Authoritative PostgreSQL, MinIO, Redis, Ollama and relay containers were preserved during application rollout. No production conversation was used to send test messages.

## Verification performed

| Check | Result |
|---|---|
| Focused Python regression suite | 127 passed |
| Recovery/controller boundary suite | 10 passed |
| Signed HTTP client tests | 3 passed |
| Real database governance fault suite | 7 passed |
| Runtime RLS | Passed unscoped denial, channel/access isolation, write denial and connection reset checks |
| Signed workspace lifecycle | Passed identity/role checks, request idempotency, durable ingestion and review |
| Chat lifecycle | Passed proposal, review, revision, saved answers, source revocation, multi-batch resume and cancellation |
| Separate actual Buzz relay | Passed signed messages, agent replies, synthesis, human-role approval, retrieval and evidence deletion |
| Migration runner | Passed repeat, atomic failure rollback and changed-history rejection |
| Offline test | Ingestion/retrieval and cached local generation passed on an internal Docker network; records survived API restart |
| Windows reliability tests | Passed |
| Alert rules | 26 rules validated successfully |
| Upgraded backup sample restore | Passed: 1,728 relay events, 231 entries, five buckets sampled, 217 delete markers |

The sample restore used backup `20260914T113937Z-d4b56e56`. An earlier pre-deployment encrypted backup is `20260914T112116Z-b11abb81`. The final release backup `20260914T120416Z-273ae846` passed encrypted-file and manifest verification (1,224 files). These are local; no off-host recovery guarantee is implied. The [release manifest](knowledge-trust-release-manifest-2026-09-14.json) records source hashes and final runtime verification without credentials.

Verification found and fixed the restore drill's startup race and its bootstrap-role mismatch. The drill now waits for PostgreSQL's final TCP listener and restores under the original bootstrap role, preserving role-grant provenance. Two old Docker image manifests were incomplete; application-only filesystem exports retained rollback images without copying mounted data or runtime environment secrets. A temporary-filesystem configuration error interrupted the first application recreation; it was corrected and all live checks subsequently passed.

## Use and remaining acceptance

See [workflow and operating instructions](knowledge-trust-implementation.md). The changes and pre-existing user edits remain uncommitted; this is a verified local release, not a published or signed production release.

The operator still needs to supply the off-host backup location, independent recovery-key custody and human alert destination/owners. The 100-case owner-reviewed quality gate is implemented but has not been satisfied with an organizational corpus. A real human desktop review session, agreed retention/deletion policy, clean-host disaster recovery and capacity testing remain acceptance work. Existing relay/database-administration and object-store credential arrangements also need separate hardening before broad enterprise production claims.
