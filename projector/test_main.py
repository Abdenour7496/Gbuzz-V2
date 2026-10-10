import unittest
import json

import main


class ProjectorTest(unittest.TestCase):
    def test_parses_buzz_imeta(self):
        tags = [["h", "channel"], ["imeta", "url http://127.0.0.1:3000/media/hash.pdf", "m application/pdf", "filename resume.pdf", "x " + "a" * 64, "size 42"]]
        self.assertEqual([{
            "url": "http://127.0.0.1:3000/media/hash.pdf",
            "media_type": "application/pdf",
            "file_name": "resume.pdf",
            "sha256": "a" * 64,
            "size": "42",
        }], main.parse_attachments(tags))

    def test_parses_asyncpg_json_text_tags(self):
        tags = json.dumps([["imeta", "url http://relay/media/item.docx", "m application/vnd.openxmlformats-officedocument.wordprocessingml.document"]])
        self.assertEqual("item.docx", main.parse_attachments(tags)[0]["url"].rsplit("/", 1)[-1])

    def test_malformed_json_tags_are_ignored(self):
        self.assertEqual([], main.parse_attachments("not-json"))

    def test_rewrites_only_media_paths(self):
        self.assertEqual("http://relay:3000/media/hash.pdf", main.internal_media_url("http://127.0.0.1:3000/media/hash.pdf"))
        with self.assertRaises(ValueError):
            main.internal_media_url("http://127.0.0.1:3000/admin")

    def test_filters_conversational_chatter(self):
        self.assertEqual((False, "conversational_chatter"), main.knowledge_disposition("@Fizz-Ceo hi"))
        self.assertEqual((False, "empty_or_mention_only"), main.knowledge_disposition("@Fizz-Ceo"))

    def test_promotes_explicit_knowledge(self):
        promote, reason = main.knowledge_disposition("Decision: retain the audit archive but filter retrieval noise")
        self.assertTrue(promote)
        self.assertEqual("knowledge_signal", reason)

    def test_promotes_substantive_discussion(self):
        promote, reason = main.knowledge_disposition(
            "Please review the Microsoft 365 configuration and explain which controls should be changed before rollout."
        )
        self.assertTrue(promote)
        self.assertEqual("substantive_message", reason)


class _StreamResponse:
    def __init__(self, content: bytes, headers=None):
        self.content = content
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self.content


class _StreamClient:
    def __init__(self, content: bytes, headers=None):
        self.response = _StreamResponse(content, headers)

    def stream(self, *_args, **_kwargs):
        return self.response


class _RetryClient(_StreamClient):
    def __init__(self, content: bytes):
        super().__init__(content)
        self.calls = 0

    def stream(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls < 2:
            raise __import__("httpx").ReadTimeout("temporary")
        return self.response


class AttachmentFetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_projection_uses_event_advisory_lock(self):
        class Connection:
            def __init__(self):
                self.locked = False
                self.statements = []

            async def fetchval(self, statement, *_args):
                self.statements.append(statement)
                if "pg_try_advisory_lock" in statement:
                    if self.locked:
                        return False
                    self.locked = True
                    return True
                return 1

            async def execute(self, statement, *_args):
                self.statements.append(statement)
                if "pg_advisory_unlock" in statement:
                    self.locked = False

        class Acquire:
            def __init__(self, connection): self.connection = connection
            async def __aenter__(self): return self.connection
            async def __aexit__(self, *_args): return None

        class Pool:
            def __init__(self): self.connection = Connection()
            def acquire(self): return Acquire(self.connection)

        pool = Pool()
        row = {"event_id": "a" * 64}
        calls = []
        original = main._process_event

        async def hold(_connection, _client, _row):
            calls.append(_row["event_id"])
            await __import__("asyncio").sleep(0.01)

        main._process_event = hold
        try:
            await __import__("asyncio").gather(
                main.process_event(pool, object(), row),
                main.process_event(pool, object(), row),
            )
        finally:
            main._process_event = original
        self.assertEqual([row["event_id"]], calls)
        self.assertTrue(any("pg_advisory_unlock" in item for item in pool.connection.statements))

    async def test_verifies_signed_size_and_hash(self):
        content = b"document"
        digest = __import__("hashlib").sha256(content).hexdigest()
        result, actual = await main.fetch_attachment(_StreamClient(content), {
            "url": f"http://desktop/media/{digest}.txt", "size": str(len(content)), "sha256": digest,
        })
        self.assertEqual(content, result)
        self.assertEqual(digest, actual)

    async def test_rejects_hash_mismatch(self):
        with self.assertRaisesRegex(ValueError, "SHA-256 does not match"):
            await main.fetch_attachment(_StreamClient(b"wrong"), {
                "url": "http://desktop/media/" + "a" * 64, "size": "5", "sha256": "a" * 64,
            })

    async def test_rejects_declared_oversize_before_download(self):
        with self.assertRaisesRegex(ValueError, "byte limit"):
            await main.fetch_attachment(_StreamClient(b""), {
                "url": "http://desktop/media/" + "a" * 64, "size": str(main.MAX_ATTACHMENT_BYTES + 1), "sha256": "a" * 64,
            })

    async def test_retries_transient_fetch_failure(self):
        client = _RetryClient(b"document")
        digest = __import__("hashlib").sha256(b"document").hexdigest()
        result, _ = await main.fetch_attachment_with_retries(client, {"url": f"http://desktop/media/{digest}.txt", "size": "8", "sha256": digest})
        self.assertEqual(b"document", result)
        self.assertEqual(2, client.calls)

    async def test_rejects_mime_extension_disagreement(self):
        with self.assertRaisesRegex(ValueError, "disagree"):
            await main.fetch_attachment(_StreamClient(b"content"), {
                "url": "http://desktop/media/" + "a" * 64, "file_name": "report.exe", "media_type": "application/pdf",
            })

    async def test_requires_signed_hash_and_size(self):
        with self.assertRaisesRegex(ValueError, "required"):
            await main.fetch_attachment(_StreamClient(b"content"), {"url": "http://desktop/media/item"})

    async def test_rejects_media_key_hash_disagreement(self):
        with self.assertRaisesRegex(ValueError, "media key"):
            await main.fetch_attachment(_StreamClient(b"content"), {
                "url": "http://desktop/media/" + "b" * 64, "size": "7", "sha256": "a" * 64,
            })

    async def test_invalidation_expires_derived_nodes(self):
        class Pool:
            async def execute(self, statement):
                self.statement = statement
                return "UPDATE 3"
        pool = Pool()
        self.assertEqual(3, await main.invalidate_stale_evidence(pool))
        self.assertIn("DELETE FROM gcor.edges", pool.statement)
        self.assertIn("DELETE FROM gcor.chunks", pool.statement)
        self.assertIn("DELETE FROM gcor.nodes", pool.statement)
        self.assertIn("superseded", pool.statement)
        self.assertIn("extraction_version", pool.statement)



class PendingEventSelectionTest(unittest.IsolatedAsyncioTestCase):
    """Regression: a failed event that never succeeded has extraction_version NULL
    and used to be re-selected on every poll, re-posting to /api/ingest forever."""

    def test_failed_rows_are_excluded_from_version_clause(self):
        self.assertIn("p.status<>'failed' AND p.extraction_version IS DISTINCT FROM $3", main.PENDING_EVENTS_SQL)
        self.assertIn("p.attempts < $4", main.PENDING_EVENTS_SQL)

    @unittest.skipUnless(__import__("os").getenv("GCOR_TEST_PG_DSN"), "set GCOR_TEST_PG_DSN to run against Postgres")
    async def test_selection_backs_off_and_parks_failed_events(self):
        import asyncpg
        connection = await asyncpg.connect(__import__("os").environ["GCOR_TEST_PG_DSN"])
        try:
            await connection.execute("""
                CREATE TEMP TABLE channels(id uuid primary key, name text, visibility text);
                CREATE TEMP TABLE events(id bytea primary key, kind int, created_at timestamptz, channel_id uuid,
                    pubkey bytea, content text, tags jsonb, deleted_at timestamptz);
                CREATE SCHEMA IF NOT EXISTS gcor;
                CREATE TABLE gcor.event_projection(event_id char(64) primary key, event_kind int not null, channel_id text,
                    status text not null, attempts int not null default 0, error text, event_created_at timestamptz not null,
                    updated_at timestamptz not null default now(), extraction_version text);
                INSERT INTO channels VALUES ('00000000-0000-0000-0000-000000000001','ops','public');""")
            cases = {
                "new": None,
                "indexed_current": ("indexed", 1, main.EXTRACTION_VERSION, 5),
                "indexed_old_version": ("indexed", 1, "older", 5),
                "failed_fresh_never_indexed": ("failed", 3, None, 1),
                "failed_backoff_elapsed": ("failed", 3, None, 100),
                "failed_backoff_pending": ("failed", 5, None, 60),
                "failed_parked": ("failed", main.MAX_ATTEMPTS, None, 99999),
                "processing_crashed": ("processing", 1, None, 30),
            }
            names = {}
            for index, (name, state) in enumerate(cases.items()):
                event_id = bytes([index + 1]) * 32
                names[event_id.hex()] = name
                await connection.execute(
                    "INSERT INTO events VALUES($1,9,now(),'00000000-0000-0000-0000-000000000001',$1,'x','[]',NULL)", event_id)
                if state:
                    status, attempts, version, age = state
                    await connection.execute(
                        """INSERT INTO gcor.event_projection(event_id,event_kind,status,attempts,event_created_at,updated_at,extraction_version)
                           VALUES($1,9,$2,$3,now(),now()-make_interval(secs=>$4),$5)""",
                        event_id.hex(), status, attempts, float(age), version)
            rows = await connection.fetch(main.PENDING_EVENTS_SQL, [9], 50, main.EXTRACTION_VERSION,
                                          main.MAX_ATTEMPTS, main.RETRY_BASE_SECONDS, main.RETRY_MAX_SECONDS)
            selected = {names[row["event_id"]] for row in rows}
            self.assertEqual({"new", "indexed_old_version", "failed_backoff_elapsed", "processing_crashed"}, selected)
        finally:
            await connection.execute("DROP TABLE IF EXISTS gcor.event_projection")
            await connection.close()

if __name__ == "__main__":
    unittest.main()
