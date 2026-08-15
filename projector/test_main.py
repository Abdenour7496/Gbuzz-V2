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


if __name__ == "__main__":
    unittest.main()
