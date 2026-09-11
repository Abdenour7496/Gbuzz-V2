import hashlib
import ipaddress
import json
import unittest
import inspect
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import main
from jsonschema import Draft202012Validator, FormatChecker


class ArchivalHelpersTest(unittest.TestCase):
    def test_document_text_is_split_into_bounded_graphiti_contributions(self):
        chunks = main.chunk_text("word " * (main.CHUNK_SIZE // 2))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= main.CHUNK_SIZE for chunk in chunks))

    def test_identity_is_scoped_by_channel_and_access(self):
        digest = "a" * 64
        first = main.document_identity(digest, "public", None, "channel-a", "General")
        self.assertEqual(first, main.document_identity(digest, "public", None, "channel-a", "General"))
        self.assertNotEqual(first, main.document_identity(digest, "public", None, "channel-b", "General"))
        self.assertNotEqual(first, main.document_identity(digest, "private", None, "channel-a", "General"))

    def test_safe_object_name_blocks_path_traversal(self):
        self.assertEqual(main.safe_object_name("../../Quarterly Report.PDF", "application/pdf"), "Quarterly-Report.PDF")

    def test_normalizes_asyncpg_json_text(self):
        self.assertEqual({"channel_id": "one"}, main.normalize_json_dict('{"channel_id":"one"}'))
        self.assertEqual({}, main.normalize_json_dict("not-json"))

    def test_governance_event_retains_recovery_provenance(self):
        from governance_outbox import build_event
        document_id = uuid4()
        event_id, bucket, key, event = build_event(document_id, "approved", {"bucket": "archive"}, "reviewer", "ok", "default")
        self.assertEqual("archive", bucket)
        self.assertIn(str(document_id), key)
        self.assertIn(str(event_id), key)
        self.assertEqual("reviewer", event["actor"])

    def test_manifest_matches_schema(self):
        record_id = uuid4()
        document_id = uuid4()
        content_digest = hashlib.sha256(b"hello").hexdigest()
        markdown_digest = hashlib.sha256(b"# hello").hexdigest()
        manifest = main.build_record_manifest(
            record_id=record_id, document_id=document_id, title="Hello", source_uri="buzz://event-1",
            media_type="text/plain", content_sha256=content_digest, markdown_sha256=markdown_digest,
            content_bytes=5, bucket="channel-a", original_key="bundle/original/hello.txt",
            markdown_key="bundle/content.md", record_key="bundle/record.json", access_level="public",
            agent_id=None, channel_name="General", channel_id="channel-a", event_id="event-1",
            event_kind="message.created", event_timestamp="2026-08-09T00:00:00Z",
            author_pubkey="author", file_url=None, file_name="hello.txt", metadata={}, status="indexed",
        )
        schema_path = Path(main.SCHEMAS_DIR) / "ingestion-record.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
        self.assertEqual([], errors)

    def test_quarantined_manifest_is_recoverable_without_document(self):
        digest = hashlib.sha256(b"binary").hexdigest()
        manifest = main.build_record_manifest(
            record_id=uuid4(), document_id=None, title="Unsupported", source_uri=None,
            media_type="application/octet-stream", content_sha256=digest,
            markdown_sha256=digest, content_bytes=6, bucket="archive",
            original_key="bundle/original/file.bin", markdown_key="bundle/content.md",
            record_key="bundle/record.json", access_level="private", agent_id=None,
            channel_name="Private", channel_id="private-1", event_id="event-2",
            event_kind="file.uploaded", event_timestamp="2026-08-10T00:00:00Z",
            author_pubkey="author", file_url=None, file_name="file.bin", metadata={},
            status="quarantined", error="unsupported format",
        )
        schema = json.loads((Path(main.SCHEMAS_DIR) / "ingestion-record.schema.json").read_text(encoding="utf-8"))
        self.assertEqual([], list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest)))
        self.assertIsNone(manifest["document_id"])


class ArchiveOnlyIngestTest(unittest.IsolatedAsyncioTestCase):
    async def test_archive_only_persists_bundle_without_embedding_or_document(self):
        s3 = MagicMock()
        pool = SimpleNamespace(execute=AsyncMock())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(s3=s3, pool=pool)))

        with patch.object(main, "ensure_bucket_name", AsyncMock()), patch.object(main, "embed", AsyncMock()) as embed:
            result = await main.ingest_payload(
                request,
                content=b"hi",
                media_type="text/plain",
                title="General message",
                access_level="private",
                agent_id=None,
                source_uri="buzz://event/test-event",
                channel_name="General",
                channel_id="channel-1",
                event_id="test-event",
                event_kind="buzz.kind.9",
                event_timestamp="2026-09-11T00:00:00Z",
                author_pubkey="author",
                file_url=None,
                file_name=None,
                metadata={"knowledge_disposition": "archived"},
                archive_only=True,
            )

        embed.assert_not_awaited()
        self.assertEqual("archived", result["status"])
        self.assertIsNone(result["document_id"])
        self.assertEqual(3, s3.put_object.call_count)
        pool.execute.assert_awaited_once()
        self.assertIn("'archived'", pool.execute.await_args.args[0])



class RemoteFetchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        resolver = patch.object(main, "resolve_host_addresses", AsyncMock(return_value=[ipaddress.ip_address("8.8.8.8")]))
        resolver.start()
        self.addCleanup(resolver.stop)

    def client_patch(self, handler):
        client_class = httpx.AsyncClient
        def client(**kwargs):
            kwargs.pop("transport", None)
            return client_class(transport=httpx.MockTransport(handler), **kwargs)
        return patch.object(main.httpx, "AsyncClient", side_effect=client)

    async def test_redirect_cannot_escape_allowed_hosts(self):
        requested = []
        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://internal.test/secret"})
        with self.client_patch(handler), patch.object(main, "REMOTE_FETCH_ALLOWED_HOSTS", ["allowed.test"]):
            with self.assertRaises(main.HTTPException) as error:
                await main.fetch_remote_file("https://allowed.test/file")
        self.assertEqual(422, error.exception.status_code)
        self.assertEqual(["https://allowed.test/file"], requested)

    async def test_relative_redirect_retains_original_filename(self):
        def handler(request):
            if request.url.path == "/file.txt":
                return httpx.Response(302, headers={"location": "/download"})
            return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain; charset=utf-8"})
        with self.client_patch(handler), patch.object(main, "REMOTE_FETCH_ALLOWED_HOSTS", ["allowed.test"]):
            result = await main.fetch_remote_file("https://allowed.test/file.txt")
        self.assertEqual((b"hello", "text/plain", "file.txt"), result)

    async def test_redirect_loop_is_bounded(self):
        requested = []
        def handler(request):
            requested.append(request)
            return httpx.Response(302, headers={"location": "/loop"})
        with self.client_patch(handler), patch.object(main, "REMOTE_FETCH_ALLOWED_HOSTS", ["allowed.test"]):
            with self.assertRaises(main.HTTPException) as error:
                await main.fetch_remote_file("https://allowed.test/loop")
        self.assertEqual(422, error.exception.status_code)
        self.assertEqual(6, len(requested))

    async def test_oversized_stream_stops_early_and_closes(self):
        class Body(httpx.AsyncByteStream):
            consumed = 0
            closed = False
            async def __aiter__(self):
                for _ in range(100):
                    self.consumed += 1
                    yield b"x" * 65536
            async def aclose(self):
                self.closed = True
        body = Body()
        with self.client_patch(lambda request: httpx.Response(200, stream=body)), \
                patch.object(main, "REMOTE_FETCH_ALLOWED_HOSTS", ["allowed.test"]), \
                patch.object(main, "MAX_INGEST_FILE_BYTES", 65536):
            with self.assertRaises(main.HTTPException) as error:
                await main.fetch_remote_file_with_retries("https://allowed.test/file")
        self.assertEqual(413, error.exception.status_code)
        self.assertEqual(2, body.consumed)
        self.assertTrue(body.closed)

    async def test_transient_failure_is_retried(self):
        requested = []
        def handler(request):
            requested.append(request)
            return httpx.Response(503 if len(requested) == 1 else 200, content=b"ok")
        with self.client_patch(handler), patch.object(main, "REMOTE_FETCH_ALLOWED_HOSTS", ["allowed.test"]), \
                patch.object(main, "ATTACHMENT_FETCH_RETRY_BACKOFF_SECONDS", 0):
            result = await main.fetch_remote_file_with_retries("https://allowed.test/file")
        self.assertEqual(2, result[3])
        self.assertEqual(b"ok", result[0])


if __name__ == "__main__":
    unittest.main()


class ChannelBucketPrefixTest(unittest.TestCase):
    def test_prefix_applies_and_respects_bucket_name_limit(self):
        import main
        original = main.CHANNEL_BUCKET_PREFIX
        try:
            main.CHANNEL_BUCKET_PREFIX = "gcor-ch-"
            self.assertEqual(main.channel_bucket_name("Release Planning"), "gcor-ch-release-planning")
            self.assertEqual(main.channel_bucket_name("ab"), "gcor-ch-ch-ab")
            long_name = main.channel_bucket_name("x" * 80)
            self.assertLessEqual(len(long_name), 63)
            self.assertTrue(long_name.startswith("gcor-ch-"))
            self.assertEqual(main.channel_bucket_name("   "), main.MINIO_BUCKET, "empty channel falls back to the GCOR bucket")
            main.CHANNEL_BUCKET_PREFIX = ""
            self.assertEqual(main.channel_bucket_name("Release Planning"), "release-planning", "unset prefix keeps historical names")
        finally:
            main.CHANNEL_BUCKET_PREFIX = original
