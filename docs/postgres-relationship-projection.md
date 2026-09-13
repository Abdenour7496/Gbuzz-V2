# PostgreSQL-native relationship projection

The optional relationship projector derives bounded entities, claims, and links
from approved-current knowledge. PostgreSQL remains the only knowledge system of
record. Derived graph data can be invalidated and rebuilt from source chunks.

The worker is dormant by default. `docker-compose.relationships.yml` must be
included and the `relationship-projection` profile explicitly enabled. This is
not production activation approval.

Every projected node and edge is bound to the source document, content SHA-256,
channel, evidence chunk ordinals, model, and deterministic run identifier. Model
output is untrusted and rejected if it contains unknown fields, missing evidence,
unknown endpoints, unsupported relations, duplicate keys, invalid confidence, or
exceeds collection/text limits. A transaction rechecks approval, evidence
currency, channel, and revision before publishing derived rows.

Superseded revisions are retained for audit but receive `valid_to`; retrieval must
select only current rows. Existing document/chunk `CONTAINS` edges remain legacy
compatible. Semantic edges added by this worker carry `channel_id` and are covered
by edge RLS.

Run the unit tests without a model or database:

```powershell
$env:PYTHONPATH = 'relationship-projector'
python -m unittest -v relationship-projector/test_main.py
```

Before activation, add production integration tests for migration application,
concurrent revision change, forced RLS, restart/idempotency, model outage/retry,
invalidation, and permission-filtered traversal against the exact deployment.
