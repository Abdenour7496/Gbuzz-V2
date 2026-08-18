# ADR-0002: Verification, observability, and reproducible releases

**Status:** Accepted
**Date:** 2026-08-15
**Deciders:** Gbuzz maintainers

## Context

Gbuzz has unit tests, health checks, recovery tooling, and a hardened production
overlay. It did not continuously prove the complete ingestion lifecycle, ship an
operator-ready metrics view, or enforce immutable production image references.

## Decision

Keep the Compose architecture and add three independent controls:

1. An isolated integration stack with deterministic local embeddings proves
   ingestion, deduplication, retrieval, and recovery-record creation in CI.
2. An opt-in observability profile supplies Prometheus, Grafana, Alertmanager,
   PostgreSQL metrics, Redis metrics, dashboards, and baseline alerts.
3. CI scans dependencies and secrets and emits an SPDX SBOM. Production image
   references are kept in an environment-specific lock overlay and verified
   before deployment.

Python input requirements remain human-maintained. The supplied lock-generation
script creates hash-locked transitive dependency files; deployments may switch
Dockerfiles to those files after reviewing and committing the generated locks.

## Options considered

| Option | Complexity | Feedback quality | Current fit |
| --- | --- | --- | --- |
| Repository-native Compose profiles and CI | Low | High | High |
| External hosted observability and build platform | Medium | High | Optional later |
| Immediate orchestrator migration | High | Unrelated to current gaps | Low |

## Consequences

- CI takes longer because it builds and starts an isolated integration stack.
- Integration tests do not assess model quality; they deliberately test pipeline
  behavior with deterministic embeddings.
- Monitoring is available locally without changing the default stack footprint.
- Alert delivery remains deployment-specific; the default receiver only records
  alerts in Alertmanager.
- Production promotion fails when mutable third-party image tags are present.

## Action items

- [x] Add the integration lifecycle gate.
- [x] Add provisioned monitoring dashboards and alerts.
- [x] Add vulnerability, secret, and SBOM CI jobs.
- [x] Add an immutable-image verification script and lock overlay template.
- [x] Generate Python lock files and enforce hash-verified installs in CI and application images.
- [ ] Configure a real Alertmanager receiver for each deployment.
