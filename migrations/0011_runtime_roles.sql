-- Runtime privilege separation. Migrations keep running as the schema owner; every
-- long-running service connects as a login role that is a member of gcor_app and
-- can only read and write data. gcor_app owns nothing, cannot create objects, is not
-- a superuser and cannot bypass row-level security. Relay tables in public are
-- readable only. The login role itself is created by the migrate step from
-- GCOR_DB_USER / GCOR_DB_PASSWORD (see migrations/runtime-role.psql).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gcor_app') THEN
        CREATE ROLE gcor_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA gcor TO gcor_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gcor TO gcor_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gcor TO gcor_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA gcor GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gcor_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA gcor GRANT USAGE, SELECT ON SEQUENCES TO gcor_app;

-- The migration ledger is operator state; services may inspect it but never change it.
CREATE TABLE IF NOT EXISTS gcor.schema_migrations (filename text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now());
REVOKE INSERT, UPDATE, DELETE ON gcor.schema_migrations FROM gcor_app;

-- Buzz relay tables (events, channels, users, channel_members) are authoritative
-- identity and evidence sources and are never written by GCOR services.
GRANT USAGE ON SCHEMA public TO gcor_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO gcor_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO gcor_app;
