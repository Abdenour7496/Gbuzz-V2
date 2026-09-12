# Backup and host recovery runbook

This runbook covers the 18-service PostgreSQL-native Gbuzz core. Graphiti/FalkorDB and `gcor.graphiti_projection` are retired compatibility data: do not start those services or delete legacy rows during recovery.

## Objectives and schedule

- Run `scripts/backup-stack.ps1` every six hours. Target RPO: at most six hours.
- Retain the latest runs from the last 24 hours, seven daily, five weekly, and twelve monthly recovery points.
- Target clean-host RTO: at most four hours after a trusted replacement host and recovery credentials are available.
- Run `scripts/test-backup-sample-restore.ps1` weekly, a clean-host drill monthly, and an owner-observed recovery-key exercise quarterly.

## Secret setup

Generate 64 random bytes, encode them as base64, and store that recovery value in an owner-controlled off-host vault. While signed in as the dedicated scheduled-task identity, run `scripts/initialize-backup-key.ps1` and enter the vaulted value at the secure prompt. The script stores a CurrentUser-DPAPI protected copy at `C:\ProgramData\Gbuzz\secrets\backup-key.dpapi` with an identity-only ACL. Never pass the value on a command line, write it to this repository, include it in a recovery manifest, or store it beside the backups. Test off-host vault access quarterly. `GBUZZ_BACKUP_KEY_BASE64` exists only as a test/automation override and should not be persisted on the production host.

The backup task runs as the interactive operating identity with limited privileges because Docker Desktop is user-session scoped. Install only after review:

```powershell
powershell -NoProfile -File scripts/initialize-backup-storage.ps1 -WhatIf
powershell -NoProfile -File scripts/initialize-backup-storage.ps1
powershell -NoProfile -File scripts/install-backup-task.ps1 -WhatIf
powershell -NoProfile -File scripts/install-backup-task.ps1
```

Backups land in `C:\ProgramData\Gbuzz\encrypted-backups`. Only `payload.gbuzzenc`, an authenticated outer `manifest.json`, and `COMPLETE` are retained. Plaintext staging uses an `.incomplete-*` directory and is removed on success or failure. The initialization script restricts the operations-directory ACLs to the operating identity, Administrators, and SYSTEM.

## What is captured

- PostgreSQL custom dump and global roles metadata (relay, identity, audit, GCOR, and application state).
- Every version and delete marker from every MinIO bucket, including `buzz-media`, stored in a content-addressed archive with an inventory.
- Relay Git data, accepted only if a before/after inventory proves it did not change during capture.
- `.env`, the exact validated Compose plan/overrides, migrations, observability configuration, Git revision, file hashes, per-component capture checkpoints, and recovery manifests. The root `docker-compose.safeguards.override.json` is a protected, restored/generated deployment artifact (derived from its tracked `.example.json` template); it must exist on the deployed host even though it is absent from a clean worktree. Backup and startup stop before mutation unless every configured required service exists in the fully resolved Compose model.

The result is **component-consistent**, not atomic across PostgreSQL, MinIO, Git, and configuration. Each component records start/end checkpoints. After capture, every PostgreSQL-referenced ingestion/governance object and every valid in-scope Buzz `imeta` media attachment must resolve to a current exported MinIO version; when an attachment supplies `x`, it must match the captured object's SHA-256. Unresolved references are written to `reference-check.json` and fail the backup gate.

Redis AOF and Ollama models are not required for authoritative recovery. Redis can rebuild operational cache/queue state from authoritative stores; Ollama models can be pulled from pinned configuration. If this assumption changes, update this runbook before relying on the RPO.

## Verification

Every backup authenticates and hashes the encrypted artifact and manifest, safely extracts the TAR with traversal/link/device rejection, verifies the exact declared evidence set and every size/SHA-256, and enforces the database-to-MinIO reference gate before success telemetry is written. `GbuzzBackupFailed`, `GbuzzBackupTooOld`, and `GbuzzBackupMetricsMissing` alert on failure, age over six hours, or absent telemetry.

Retention is applied before capacity admission. Eligible expired points are pruned first; protected 24-hour/daily/weekly/monthly points are never deleted to satisfy the byte cap. If protected retention plus the next encrypted artifact estimate exceeds `MaxLocalBytes`, the run fails before promotion and reports that capacity/retention policy must be changed by an owner.

```powershell
powershell -NoProfile -File scripts/verify-backup.ps1 -BackupPath C:\ProgramData\Gbuzz\encrypted-backups\<run>
powershell -NoProfile -File scripts/test-backup-sample-restore.ps1 -BackupPath C:\ProgramData\Gbuzz\encrypted-backups\<run>
```

The weekly test decrypts into a bounded OS temporary directory, performs full inner-manifest verification, restores PostgreSQL into a disposable isolated container, validates relay and GCOR tables, verifies at least one recoverable version from every non-empty MinIO bucket, retains delete-marker evidence, and lists the Git archive. It never connects restored services to production.

## Clean-host drill

1. Provision a trusted isolated host; install the reviewed Docker Desktop/runtime version and this exact Git revision.
2. Retrieve one encrypted recovery point and its manifest from the approved off-host destination. Retrieve the key through the separate custodian process.
3. Verify and decrypt. Create empty named volumes; do not mount production volumes.
4. Restore PostgreSQL data and roles, every MinIO bucket/version/delete marker, and relay Git data. Validate manifest hashes and object/repository counts.
5. Start the exact approved Compose overlays. Do not include Graphiti/FalkorDB overlays.
6. Verify relay authentication/readiness, media download from `buzz-media`, Git clone, message-to-knowledge ingestion, provenance-bearing retrieval, governance publication, audit logging, all Prometheus targets, backup telemetry, and alert resolution.
7. Record achieved RPO/RTO, evidence hashes, deviations, and approvers. Destroy the drill environment only under the approved cleanup procedure.

Production restoration, database rollback, credential rotation, cutover, firewall changes, and record deletion always require explicit owner authorization.

## Off-host adapter

`scripts/offhost-upload.ps1` is fail-closed unless `-Approved` is supplied. Configure exactly one approved destination through environment variables and provider-native identity; never embed credentials in arguments. Require encryption at rest, transport encryption, versioning/object lock (minimum 30 days where supported), delete protection, a separate account/failure domain, and access logging. The adapter does not establish that policy itself—verify destination controls before first transfer.

## Rollback

Before activation, rollback is simply not installing the tasks. After activation, disable the two `Gbuzz-*` scheduled tasks; do not remove encrypted recovery points. Revert the observability deployment to the prior reviewed Compose/alerts revision if backup telemetry causes a monitoring regression. Never delete volumes or restore data as rollback for these host scripts.
