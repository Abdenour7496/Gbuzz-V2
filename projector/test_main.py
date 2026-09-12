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
        self.assertIn("valid_to=now()", pool.statement)
        self.assertIn("superseded", pool.statement)


if __name__ == "__main__":
    unittest.main()
