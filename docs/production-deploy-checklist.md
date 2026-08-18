# Gbuzz Production Deploy Checklist

**Release revision:** `<git SHA>`
**Deployment:** `<name/environment>`
**Release owner:** `<name>`
**Rollback owner:** `<name>`
**Change window:** `<start/end with timezone>`

Record evidence or a link beside every checked item. Do not treat this file as
approval to expose a service publicly or to replace production secrets.

## Pre-deploy gate

- [ ] The exact release revision is reviewed, committed, and has a green CI run.
- [ ] No unresolved critical defect or HIGH/CRITICAL security finding affects the release.
- [ ] The CI SBOM artifact is retained with the release evidence.
- [ ] `requirements.lock` changes are reviewed for every changed Python dependency.
- [ ] `docker-compose.production.lock.yml` contains the approved image digests and commit-addressed Gbuzz images.
- [ ] `scripts/verify-production-pins.ps1` passes against the deployment lock overlay.
- [ ] Secrets come from the deployment platform; `.env` and backup files are absent from the release artifact.
- [ ] External endpoints terminate behind deployment-approved TLS and authentication.
- [ ] GCOR proxy, GCOR MCP, Graphiti MCP, and recovery-controller endpoints are not publicly reachable.
- [ ] PostgreSQL and MinIO backups completed, checksums were recorded, and the latest restore exercise met the RTO/RPO.
- [ ] Migration `0006_graphiti_bounded_episodes.sql` and all earlier unapplied migrations passed in staging.
- [ ] The migration owner recorded the forward-only/rollback decision and authoritative-data recovery point.
- [ ] Alertmanager has a tested deployment receiver and an acknowledged test notification.
- [ ] Disk-capacity, readiness, error-rate, latency, projector-lag, and recovery-budget alerts are active.
- [ ] Staging passed the isolated lifecycle test and the recovery/escalation exercise.

## Deploy

- [ ] Record the pre-deploy PostgreSQL migration version and Graphiti dead-letter/in-flight counts.
- [ ] Run migrations exactly once as the designated one-shot migration owner.
- [ ] Confirm the migration container exits `0` before starting application services.
- [ ] Deploy the immutable lock overlay with `docker-compose.production.yml`.
- [ ] Confirm PostgreSQL, Redis, MinIO, Ollama, FalkorDB, relay, GCOR, Graphiti, and recovery-controller health.
- [ ] Verify relay readiness, GCOR health, recovery status, Prometheus readiness, and Alertmanager readiness.
- [ ] Run an authenticated ingestion/retrieval smoke test in a non-sensitive deployment channel.
- [ ] Confirm the recovery bundle exists in MinIO and retrieval returns provenance-bearing citations.
- [ ] Run `scripts/graphiti-projection-release-gate.ps1`; it must report zero `pending`, `retryable`, `in_flight`, and `dead_letter` entries.
- [ ] Watch readiness, error rate, latency, disk, projector lag, and recovery actions for at least 15 minutes.

## Rollback triggers

Rollback or stop promotion if any of the following occurs:

- A migration fails or authoritative-data validation differs from the pre-deploy record.
- Relay, GCOR, PostgreSQL, or MinIO is not healthy within 10 minutes.
- The authenticated ingestion/retrieval smoke test fails.
- Graphiti `dead_letter` is non-zero or projector lag grows continuously for 10 minutes.
- Any service consumes the recovery action budget or repeats unhealthy/restart cycles.
- GCOR HTTP 5xx rate exceeds 5% for 5 minutes or p95 HTTP latency exceeds 2 seconds for 5 minutes.
- Security boundaries expose a private service or authentication cannot be verified.

Application rollback means redeploying the prior immutable image lock. Do not
reverse a database migration unless its reviewed rollback procedure explicitly
supports reversal. Restore authoritative data only under the named recovery
owner's runbook; FalkorDB remains a rebuildable projection.

## Post-deploy

- [ ] Confirm all alerts are nominal and no notification is left unacknowledged.
- [ ] Record deployed image digests, migration version, smoke evidence, and dashboard snapshot.
- [ ] Retain the SBOM, CI URL, dependency lock diff, backup evidence, and operator names.
- [ ] Notify stakeholders of completion or rollback.
- [ ] Schedule the next restore and recovery exercise before closing the release.
