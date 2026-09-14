# PostgreSQL-native relationship projection

The optional relationship projector derives bounded entities, claims, and links
from approved-current knowledge. PostgreSQL remains the only knowledge system of
record. Derived graph data can be invalidated and rebuilt from source chunks.

The worker is dormant by default. `docker-compose.relationships.yml` must be
included and the `relationship-projection` profile explicitly enabled. This is
not production activation approval. The optional migration is stored outside the
core migration directory and is applied only by the profile's one-shot migrator.
Before activation, an owner-controlled provisioning step must create the login
named by `GCOR_RELATIONSHIP_DB_USER`, grant it only the non-login
`gcor_relationship_projector` role, and set its secret through the deployment
secret mechanism. The worker never uses `GCOR_DB_USER` or the database owner.

Every projected node and edge is bound to the source document, content SHA-256,
channel, evidence chunk ordinals, model, and deterministic run identifier. Model
output is untrusted and rejected if it contains unknown fields, missing evidence,
unknown endpoints, unsupported relations, duplicate keys, invalid confidence, or
exceeds collection/text limits. A transaction rechecks approval, evidence
currency, channel, revision, exact chunk snapshot, and lease ownership before
publishing derived rows. Failed work uses bounded exponential backoff and stops
after `RELATIONSHIP_MAX_ATTEMPTS` (default 8) until an operator or source revision
explicitly makes it eligible again.

Superseded revisions are retained for audit but receive `valid_to`; retrieval must
select only current rows. Existing document/chunk `CONTAINS` edges remain legacy
compatible. Semantic edges added by this worker carry `channel_id` and are covered
by edge RLS.

Run the unit tests without a model or database:

```powershell
$env:PYTHONPATH = 'relationship-projector'
python -m unittest -v relationship-projector/test_main.py
```

CI applies the optional migration in a disposable database and proves the normal
runtime role cannot read an unscoped relationship ledger while the dedicated role
cannot read unrelated Audit Pack state. Before activation, extend production-like
coverage for concurrent revision change, restart/idempotency, model outage/retry,
invalidation, and permission-filtered traversal against the exact deployment.
