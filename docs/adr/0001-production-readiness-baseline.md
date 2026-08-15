# ADR-0001: Production-readiness baseline

**Status:** Accepted
**Date:** 2026-08-14
**Deciders:** Gbuzz maintainers

## Context

Gbuzz is a stateful Compose deployment with PostgreSQL as the system of record,
Redis and object storage, rebuildable graph projections, and several Python
services. The stack already has dependency health checks and recovery tooling,
but did not define a repeatable CI gate or production runtime constraints.

## Decision

Use the existing Compose architecture for the current maturity stage and add a
production overlay. Every change must pass unit tests and rendered Compose
validation. Production application containers run with no-new-privileges,
dropped Linux capabilities, bounded resources and log rotation. Automatic relay
migrations are disabled in production; migrations remain an explicit one-shot
service. Public exposure is opt-in through bind-address variables.

PostgreSQL remains authoritative. FalkorDB is a rebuildable projection and is
not part of the recovery point objective. Production images must be pinned to an
immutable digest by the deployment environment, even where local defaults remain
developer-friendly tags.

## Options considered

| Option | Complexity | Cost | Scalability | Current fit |
| --- | --- | --- | --- | --- |
| Retain and harden Compose | Low | Low | Limited | High |
| Move immediately to Kubernetes | High | High | High | Unknown |

Kubernetes would add scheduling and rollout primitives but would not itself fix
backup verification, dependency pinning, observability or release discipline.
Compose is retained until measured availability or scaling needs justify the
additional control plane.

## Consequences

- Local development stays simple while production receives stricter defaults.
- Deployments must run migrations deliberately before application rollout.
- Operators must size the default resource limits for their workload.
- A future orchestrator migration remains possible because services retain clear
  health checks and stateless application boundaries.

## Production gate

- [ ] CI passes on the exact revision being deployed.
- [ ] `BUZZ_IMAGE` and third-party images are pinned by digest.
- [ ] Secrets are supplied by the deployment platform, not committed files.
- [ ] Database and object-store backups have a tested restore within the RTO.
- [ ] Migrations are run once and a rollback decision is recorded.
- [ ] TLS/authentication terminates before every externally reachable endpoint.
- [ ] Alerts cover readiness, error rate, latency, disk capacity and projector lag.
- [ ] A named operator owns the release and rollback.

