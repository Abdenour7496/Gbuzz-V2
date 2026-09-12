# Application trust boundary

Production sets `REQUIRE_WORKLOAD_IDENTITY=true`. A shared webhook secret is then
insufficient. Each HTTP workload presents its own bearer token, an explicit channel
UUID and access classification. Only token hashes and operation allowlists are held
by the proxy. The projector credential has only `ingest`; retrieval, reply and Audit
Pack export require distinct credentials. Automated Audit Pack creation additionally
requires a signed/approved request event identifier in `approval_id`.

Interactive NIP-98 proofs are URL, method and payload bound, accepted once within the
freshness window, and membership is checked both before execution and immediately
before response release. Callers must treat a release-time 403 as authoritative and
discard any locally staged response.

Runtime database access fails closed without a channel scope. Explicit internal queue
workers set named workload scopes captured by their tasks; their exceptional policies
cover only the tables needed for ingestion or governance publication.

## Isolated parser contract

Hostile document parsing must move to a separate service before attachment ingestion
is released. The parser service must have no network, run as a non-root UID with all
capabilities dropped, `no-new-privileges`, a read-only filesystem and bounded tmpfs,
and enforce per-job CPU, memory, process, input, expanded-output, extracted-text and
wall-clock limits. The API sends bytes plus mandatory signed size/digest and expected
media key; the worker independently hashes, sniffs type, verifies exact key/digest
agreement, scans/quarantines, parses, and returns only anchored text plus a versioned
manifest. It never receives database, MinIO, relay, model, signing or workload secrets.
Failures return a typed quarantine reason and no partial chunks. Workers/temp data are
destroyed after each job. PDF/Office/image native libraries and malware signatures are
digest pinned and included in release SBOM/vulnerability evidence.

## Remaining implementation gate

This branch specifies the parser boundary but does not copy the concurrently developed
attachment parser into this worktree. Integration must occur only after the attachment
commit is independently reviewed, then the API-process parsers must be removed.
