# Parser worker operations

The parser is fail-closed. `unavailable` is critical after one event; more than
five timeout/rejection outcomes in 15 minutes or more than four in-flight jobs
for five minutes requires pausing attachment promotion and inspecting only
digest/event IDs. Never log source or extracted text.

Restart the isolated worker, verify its socket owner/mode and container controls,
then replay failed event projections. Replay is keyed by event, source digest,
and extraction version. Reconciliation removes stale derivatives atomically;
immutable source objects and ingestion records remain governed evidence.

Do not enable image OCR until the OCR worker image has current SBOM, license,
HIGH/CRITICAL scan, dimension/pixel limit, and no-network evidence.
