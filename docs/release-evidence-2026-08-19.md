# Gbuzz Release-Candidate Evidence — 2026-08-19

**Candidate base revision:** `c75dd3d94cda3a28fe9fb7de7eb616a5ca9fa55d` plus the uncommitted changes listed by `git status --short`  
**Environment:** Windows host; Docker Desktop 29.5.2; Python 3.12 Linux images built from the checked-in hash-locked requirements  
**Exercise operator:** Codex local execution on behalf of the workspace owner  
**Decision:** **NO-GO** until every blocker below is resolved and the final committed revision is reverified

This record reports local release-candidate verification. It is evidence, not
production approval. No commit, push, image publication, or deployment occurred.

## Candidate hygiene

- **PASS — generated artifact separation.** Three database backups remain under
  ignored `backups/`; the previously root-level ZIP was moved to
  `backups/archives/`. These local artifacts are not candidate source files.
- **PASS — release artifact input gate.**
  `scripts/verify-release-artifact.ps1` reported: `Release artifact input gate
  passed: 91 candidate files; no generated backup/archive artifacts.`
- **PASS — Docker context exclusions.** Root build contexts exclude `backups/`,
  `*.zip`, `*.tar`, `*.tgz`, `*.dump`, and `*.bak`. CI now runs the same
  prospective-candidate gate before release jobs can publish images.
- **PASS — patch hygiene.** `git diff --check` exited 0.

## Passing local gates

- **PASS — full production Compose rendering.** The CI-equivalent command using
  `.env.example`, the base, observability, production, and production lock-example
  overlays, and the observability profile exited 0.
- **PASS — immutable local pins.** `scripts/verify-production-pins.ps1` verified
  22 immutable production image references.
- **PASS — recovery-controller unit tests.** 6 tests passed in 0.011 seconds.
- **PASS — GCOR proxy unit tests.** 7 tests passed in 0.008 seconds in the
  hash-locked Linux image.
- **PASS — event-projector unit tests.** 2 tests passed in 0.001 seconds in the
  hash-locked Linux image.
- **PASS — Graphiti-projector unit tests.** 7 tests passed in 0.002 seconds in
  the hash-locked Linux image.
- **PASS — isolated integration lifecycle.** The integration stack built from
  the candidate, migrations completed, services became healthy, and the smoke
  container reported `integration smoke test passed` with exit code 0. The
  containers, network, and volumes were then removed successfully.

## Blocking gates

- **BLOCKED — clean source provenance.** The candidate is intentionally left
  uncommitted for review. Record and verify the final commit SHA before release.
- **BLOCKED — registry provenance.** `verify-production-pins.ps1 -RequireRegistry`
  exited 1 because seven Gbuzz application images still use local
  `gbuzz-* @sha256` references rather than published registry references:
  `gcor-event-projector`, `gcor-proxy`, `recovery-controller`, `alert-receiver`,
  `mcp-postgres-gcor`, `graphiti-mcp`, and `graphiti-projector`.
- **BLOCKED — production ownership.** `scripts/production-preflight.ps1
  -AllowDirtyTree -SkipProjectionGate` exited 1 at the first missing assignment:
  `Production ownership is not assigned: RELEASE_OWNER`. Release, rollback,
  migration, and on-call ownership must all be assigned in deployment config.
- **BLOCKED — disk capacity.** The workspace filesystem had 0.54% free space
  (1.29 GiB), below the implemented 15% minimum.
- **BLOCKED — projection backlog.** The projection gate exited 1 with
  `pending=107, retryable=5, in_flight=1, dead_letter=0`; all four release-gate
  buckets must be zero.
- **BLOCKED — deployment Alertmanager configuration.** The local `.env` does not
  define `ALERTMANAGER_CONFIG_FILE`; the full production Compose rendering was
  therefore verified with the checked-in example inputs only. Production must
  supply and test a deployment-specific non-local receiver.
- **BLOCKED — external CI/security evidence.** No remote CI run, Trivy result,
  gitleaks result, SBOM artifact, review approval, or published image manifest is
  available for this uncommitted candidate.

## Release decision and next evidence

**NO-GO.** After review, commit the focused change set, publish the seven
application images, update the production lock to registry digests, assign the
four operational owners, configure and exercise the external alert receiver,
restore at least 15% free disk capacity, drain the projection backlog, and run
the complete preflight against the exact committed SHA. Retain the green CI URL,
security scan results, SBOM, release image manifest, and owner acknowledgements
with this record.
