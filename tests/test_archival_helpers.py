import hashlib
import json
import unittest
import inspect
from pathlib import Path
from uuid import uuid4

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

    def test_governance_writer_returns_bucket_and_key(self):
        source = inspect.getsource(main.write_governance_event)
        self.assertIn("return bucket, key", source)

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



if __name__ == "__main__":
    unittest.main()
