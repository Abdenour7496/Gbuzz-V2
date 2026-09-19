#!/bin/sh
set -eu
case "${EMBEDDING_DIMS:-}" in ''|*[!0-9]*) echo 'EMBEDDING_DIMS must be a positive integer' >&2; exit 1;; esac
[ "$EMBEDDING_DIMS" -gt 0 ]
psql -v ON_ERROR_STOP=1 -c 'CREATE SCHEMA IF NOT EXISTS gcor; CREATE TABLE IF NOT EXISTS gcor.schema_migrations (filename text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())'
script=$(mktemp)
normalized=$(mktemp)
trap 'rm -f "$script" "$normalized"' EXIT
directory=${MIGRATIONS_DIR:-/migrations}
for migration in "$directory"/*.sql; do
    name=$(basename "$migration")
    case "$name" in *[!a-z0-9_.]*) echo 'Invalid migration filename' >&2; exit 1;; esac
    # Git checkouts on Windows can use CRLF. Treat line-ending-only changes as
    # identical while still rejecting any changed migration content.
    sed 's/\r$//' "$migration" > "$normalized"
    sum=$(sha256sum "$normalized" | cut -d' ' -f1)
    raw_sum=$(sha256sum "$migration" | cut -d' ' -f1)
    windows_sum=$(sed 's/$/\r/' "$normalized" | sha256sum | cut -d' ' -f1)
    # One session holds the lock, checks the ledger and commits DDL with its record.
    cat > "$script" <<EOF
BEGIN;
SELECT pg_advisory_xact_lock(93467123);
SELECT EXISTS(SELECT 1 FROM gcor.schema_migrations WHERE filename='$name') AS recorded,
       EXISTS(SELECT 1 FROM gcor.schema_migrations WHERE filename='$name' AND sha256 IN ('$sum','$windows_sum','$raw_sum')) AS matches \gset
\if :recorded
  \if :matches
    \echo skip $name
    UPDATE gcor.schema_migrations SET sha256='$sum' WHERE filename='$name' AND sha256<>'$sum';
  \else
    \echo ERROR: Recorded migration changed: $name. Add a new migration instead.
    SELECT 1/0;
  \endif
\else
\echo apply $name
EOF
    sed "s/__EMBEDDING_DIMS__/$EMBEDDING_DIMS/g" "$normalized" >> "$script"
    cat >> "$script" <<EOF

INSERT INTO gcor.schema_migrations(filename,sha256) VALUES('$name','$sum');
\endif
COMMIT;
EOF
    psql -v ON_ERROR_STOP=1 -f "$script"
done
if [ -n "${GCOR_DB_PASSWORD:-}" ]; then
    psql -v ON_ERROR_STOP=1 -v user="${GCOR_DB_USER:-gcor_runtime}" -v pw="$GCOR_DB_PASSWORD" -f "$directory/runtime-role.psql"
    echo 'Restricted runtime role ready'
fi
