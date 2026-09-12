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
- [ ] LAN deployments use `docker-compose.lan.yml`, an explicit RFC1918 host address, and a Windows firewall rule limited to the approved client CIDR.
- [ ] Secret-bearing paths pass `protect-gbuzz-secrets.ps1 -Mode Audit` for only the operating identity, Administrators and SYSTEM.
- [ ] The resolved Compose JSON, per-image SPDX 2.3 SBOM and approved-scanner SARIF are bound one-to-one to every runtime/build digest by `new-release-security-evidence.ps1`; its protected configuration pins the owner-authenticated policy ID/version/digest/key and canonical deployment repository.
- [ ] Fresh `activation_probe` evidence is signed by two distinct owner-pinned external probe keys: an inside-CIDR allowed connection and an outside-CIDR denial, both bound to the exact deployment/release, Compose digest, firewall-policy digest, run/nonce, relay endpoint, and approved CIDR. Modeled evidence cannot satisfy this gate; a compliant local Windows rule without both proofs is `compliant:false`.
- [ ] `scripts/verify-production-pins.ps1` passes against the deployment lock overlay.
- [ ] Secrets come from the deployment platform; `.env` and backup files are absent from the release artifact.
- [ ] External endpoints terminate behind deployment-approved TLS and authentication.
- [ ] GCOR proxy, GCOR MCP, Graphiti MCP, and recovery-controller endpoints are not publicly reachable.
- [ ] PostgreSQL and MinIO backups completed, checksums were recorded, and the latest restore exercise met the RTO/RPO.
- [ ] `GCOR_DB_USER`/`GCOR_DB_PASSWORD` are set; the running proxy reports `rolsuper`, `rolbypassrls` and `rolcreaterole` as false (see production safeguards).
- [ ] `GCOR_S3_ACCESS_KEY`/`GCOR_S3_SECRET_KEY`/`GCOR_CHANNEL_BUCKET_PREFIX` are set and `mc admin user info` shows only the `gcor-service` policy; `docker compose ps` shows the socket mounted only in `docker-socket-proxy`.
- [ ] Migration `0012_row_level_security.sql` and all earlier unapplied migrations passed in staging; `SELECT filename FROM gcor.schema_migrations` lists every file.
- [ ] Governance publication backlog is drained or retained for retry; the approved rollback plan preserves the outbox table.
- [ ] Remote URL fetches reject private/metadata destinations, environment proxies, and DNS rebinding in staging.
- [ ] The migration owner recorded the forward-only/rollback decision and authoritative-data recovery point.
- [ ] Alertmanager has a tested deployment receiver and an acknowledged test notification.
- [ ] Disk-capacity, readiness, error-rate, latency, projector-lag, and recovery-budget alerts are active.
- [ ] Staging passed the isolated lifecycle test and the recovery/escalation exercise.

## Deploy

- [ ] Record the pre-deploy PostgreSQL migration version and Graphiti dead-letter/in-flight counts.
- [ ] Run migrations exactly once as the designated one-shot migration owner.
- [ ] Confirm the migration container exits `0` before starting application services.
- [ ] Deploy the immutable lock overlay with `docker-compose.production.yml`. Canonical file order: `docker-compose.yml`, `docker-compose.safeguards.override.json` (if a guarded rollout created it), feature overlays (`enterprise`, `buzz`, `graph` + `graph-production`), `docker-compose.observability.yml` + `docker-compose.observability-production.yml` with `--profile observability`, then `docker-compose.production.yml` and the lock file last.
- [ ] Include the enterprise and observability overlays when operating those enabled services; preserve the managed core image override and do not remove their containers as orphans.
- [ ] Verify signed workspace role boundaries, ingestion worker heartbeat and document reader restrictions.
- [ ] Confirm PostgreSQL, Redis, MinIO, Ollama, relay, GCOR, and recovery-controller health. Check FalkorDB and Graphiti only when the optional graph extension is enabled.
- [ ] Verify relay readiness, GCOR health, recovery status, Prometheus readiness, and Alertmanager readiness.
- [ ] Run an authenticated ingestion/retrieval smoke test in a non-sensitive deployment channel.
- [ ] Confirm the recovery bundle exists in MinIO and retrieval returns provenance-bearing citations.
- [ ] For graph-enabled deployments, run `scripts/graphiti-projection-release-gate.ps1`; it must report zero `pending`, `retryable`, `in_flight`, and `dead_letter` entries. Core-only deployments retain pending projection work and skip this gate. See [optional graph](optional-graph.md).
- [ ] Watch readiness, error rate, latency, disk, projector lag, and recovery actions for at least 15 minutes.

## Rollback triggers

Rollback or stop promotion if any of the following occurs:

- A migration fails or authoritative-data validation differs from the pre-deploy record.
- Relay, GCOR, PostgreSQL, or MinIO is not healthy within 10 minutes.
- The authenticated ingestion/retrieval smoke test fails.
- When the graph extension is enabled, Graphiti `dead_letter` is non-zero or projector lag grows continuously for 10 minutes.
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
