import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT=Path(__file__).parents[1]/"scripts"/"check-minio-references.py"


class MinioReferenceTests(unittest.TestCase):
    def run_rows(self, rows, versions):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);refs=root/"refs.jsonl";inventory=root/"inventory.json";output=root/"report.json"
            refs.write_text("".join(json.dumps(row)+"\n" for row in rows));inventory.write_text(json.dumps({"versions":versions}))
            result=subprocess.run([sys.executable,str(SCRIPT),str(refs),str(inventory),str(output)],capture_output=True,text=True)
            return result.returncode,json.loads(output.read_text())

    def run_check(self,current=True):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);refs=root/"refs.jsonl";inventory=root/"inventory.json";output=root/"report.json"
            refs.write_text(json.dumps({"source":"test","id":"1","bucket":"buzz-media","key":"a"})+"\n")
            inventory.write_text(json.dumps({"versions":[{"bucket":"buzz-media","key":"a","is_latest":current,"delete_marker":False},{"bucket":"buzz-media","key":"gone","is_latest":True,"delete_marker":True}]}))
            result=subprocess.run([sys.executable,str(SCRIPT),str(refs),str(inventory),str(output)],capture_output=True,text=True)
            return result.returncode,json.loads(output.read_text())

    def test_current_reference_passes_and_delete_marker_is_counted(self):
        code,report=self.run_check();self.assertEqual(0,code);self.assertTrue(report["passed"]);self.assertEqual(1,report["delete_markers"])

    def test_unresolved_reference_fails_gate(self):
        code,report=self.run_check(False);self.assertNotEqual(0,code);self.assertFalse(report["passed"]);self.assertEqual(1,len(report["unresolved"]))

    def test_valid_buzz_attachment_requires_current_matching_digest(self):
        digest="a"*64;row={"source":"buzz_event_imeta","id":"event","bucket":"buzz-media","tags":[["imeta",f"url http://relay/media/{digest}.zip",f"x {digest}"]]}
        version={"bucket":"buzz-media","key":f"{digest}.zip","sha256":digest,"is_latest":True,"delete_marker":False}
        code,report=self.run_rows([row],[version]);self.assertEqual(0,code);self.assertEqual(1,report["references_checked"])

    def test_missing_buzz_attachment_fails(self):
        digest="b"*64;row={"source":"buzz_event_imeta","id":"event","tags":[["imeta",f"url /media/{digest}.bin",f"x {digest}"]]}
        code,report=self.run_rows([row],[]);self.assertNotEqual(0,code);self.assertEqual(1,len(report["unresolved"]))

    def test_non_media_tags_are_ignored(self):
        row={"source":"buzz_event_imeta","id":"event","tags":[["imeta","url https://example.test/file","x nope"],["p","abc"]]}
        code,report=self.run_rows([row],[]);self.assertEqual(0,code);self.assertEqual(0,report["references_checked"])

    def test_malformed_media_tag_fails_closed(self):
        row={"source":"buzz_event_imeta","id":"event","tags":[["imeta","url /media/not-a-digest.bin"]]}
        code,report=self.run_rows([row],[]);self.assertNotEqual(0,code);self.assertEqual("malformed media attachment digest",report["unresolved"][0]["invalid_reason"])

    def test_buzz_attachment_hash_mismatch_fails(self):
        expected="c"*64;actual="d"*64;row={"source":"buzz_event_imeta","id":"event","tags":[["imeta",f"url /media/{expected}.bin",f"x {expected}"]]};version={"bucket":"buzz-media","key":f"{expected}.bin","sha256":actual,"is_latest":True,"delete_marker":False}
        code,report=self.run_rows([row],[version]);self.assertNotEqual(0,code);self.assertEqual(expected,report["unresolved"][0]["sha256"])


if __name__=="__main__":unittest.main()
