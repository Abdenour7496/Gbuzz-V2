#!/bin/sh
set -eu
[ "$PGDATABASE" = integration ] || { echo 'Isolated integration database required' >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp /migrations/* "$work/"
export MIGRATIONS_DIR="$work"
if ! sh "$work/apply.sh" > "$work/repeat.log" 2>&1; then tail -20 "$work/repeat.log"; exit 1; fi
cat > "$work/9999_fault_probe.sql" <<'EOF'
CREATE TABLE gcor.migration_atomicity_probe(id integer);
SELECT 1/0;
EOF
if sh "$work/apply.sh" > "$work/fault.log" 2>&1; then echo 'Fault migration unexpectedly succeeded'; exit 1; fi
if [ "$(psql -Atc "SELECT to_regclass('gcor.migration_atomicity_probe') IS NULL AND NOT EXISTS(SELECT 1 FROM gcor.schema_migrations WHERE filename='9999_fault_probe.sql')")" != t ]; then tail -20 "$work/fault.log"; exit 1; fi
rm "$work/9999_fault_probe.sql"
printf '\n-- substantive edit forbidden after deployment\n' >> "$work/0015_knowledge_loop_durability.sql"
if sh "$work/apply.sh" > "$work/change.log" 2>&1; then echo 'Changed recorded migration unexpectedly succeeded'; exit 1; fi
echo 'PASS: repeat is safe, failed DDL and ledger roll back together, changed recorded migrations are rejected'
