# ADR-0003: Bounded stack health and recovery controller

**Status:** Accepted
**Date:** 2026-08-15
**Deciders:** Gbuzz maintainers

## Context

Gbuzz has container health checks, restart policies, Prometheus alerts, and
recovery artifacts. A crashed container is normally restarted by Docker, but an
unhealthy process that remains running is not. Alertmanager also cannot repair
the stack, and a Buzz-hosted SRE agent depends on parts of the stack it would be
asked to recover.

The recovery mechanism must keep working when the relay or knowledge services
are unavailable. It must not turn an LLM agent into an unrestricted Docker
administrator or automatically mutate authoritative data.

## Decision

Run a small `recovery-controller` service in the default Compose deployment. It
observes only containers carrying the stack's recovery labels. After a
configurable number of consecutive unhealthy observations it may restart a
container only when its label explicitly selects the `restart` action.

PostgreSQL, Redis, MinIO, Ollama, and FalkorDB are monitor-only. The relay and
stateless application services may be restarted. Cooldowns and a rolling action
budget prevent loops. Every decision is written to an append-only JSONL audit
volume and exported as Prometheus metrics. A read-only status API exposes the
current assessment to operators and the Buzz SRE agent.

The controller uses the Docker Engine socket. Although mounted read-only at the
filesystem layer, the socket is a privileged control API. The controller is
therefore deliberately dependency-free, has no general command endpoint, uses
an allow-list expressed in immutable Compose labels, drops Linux capabilities,
and runs with a read-only root filesystem. Deployments requiring a stronger
boundary should place a Docker socket proxy in front of it or run the same
controller against a restricted orchestrator API.

## Options considered

| Option | Independence | Recovery capability | Operational cost |
| --- | --- | --- | --- |
| Buzz SRE agent directly controls Docker | Low | Broad and unsafe | Medium |
| Prometheus/Alertmanager only | High | None | Low |
| Bounded independent controller plus SRE agent | High | Narrow and auditable | Medium |
| Immediate migration to Kubernetes operators | High | Broad | High |

## Consequences

- Recovery continues when Buzz or GCOR is unavailable.
- Stateful recovery remains a human-approved runbook.
- The SRE agent can explain status and audit evidence without privileged shell
  access.
- The Docker socket remains a sensitive deployment dependency.
- Application-level degradation still needs explicit health checks and metrics;
  process liveness alone cannot detect every failure.

## Action items

- [x] Implement the bounded controller, audit log, status API, and metrics.
- [x] Label managed services and add it to the default Compose stack.
- [x] Add Prometheus scraping, alerts, and dashboard panels.
- [ ] Configure a deployment-specific Alertmanager notification receiver.
- [ ] Exercise recovery and escalation scenarios in a staging environment.
