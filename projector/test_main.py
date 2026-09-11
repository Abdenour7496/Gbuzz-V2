import unittest

import main


class ProjectorTest(unittest.TestCase):
    def test_parses_buzz_imeta(self):
        tags = [["h", "channel"], ["imeta", "url http://127.0.0.1:3000/media/hash.pdf", "m application/pdf", "filename resume.pdf"]]
        self.assertEqual([{
            "url": "http://127.0.0.1:3000/media/hash.pdf",
            "media_type": "application/pdf",
            "file_name": "resume.pdf",
        }], main.parse_attachments(tags))

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


if __name__ == "__main__":
    unittest.main()
