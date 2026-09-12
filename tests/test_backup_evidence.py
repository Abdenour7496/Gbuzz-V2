import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import importlib.util

SCRIPT = Path(__file__).parents[1] / "scripts" / "backup-evidence.py"
SPEC = importlib.util.spec_from_file_location("backup_evidence", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
extract_and_verify = MODULE.extract_and_verify


class BackupEvidenceTests(unittest.TestCase):
    def make_archive(self, folder: Path, *, bad_hash=False, missing=False, extra=False, traversal=False):
        evidence={"reference-check.json":json.dumps({"passed":True,"unresolved":[],"delete_markers":1}).encode(),"data.bin":b"payload"}
        declared=[]
        for name,data in evidence.items():
            if missing and name=="data.bin":continue
            declared.append({"path":name,"size":len(data),"sha256":("0"*64 if bad_hash and name=="data.bin" else hashlib.sha256(data).hexdigest())})
        manifest=json.dumps({"format":2,"files":declared}).encode();archive=folder/"payload.tar"
        with tarfile.open(archive,"w") as tar:
            for name,data in evidence.items():
                info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
            if extra:
                data=b"extra";info=tarfile.TarInfo("unexpected.bin");info.size=len(data);tar.addfile(info,io.BytesIO(data))
            if traversal:
                data=b"escape";info=tarfile.TarInfo("../escape");info.size=len(data);tar.addfile(info,io.BytesIO(data))
            info=tarfile.TarInfo("recovery-manifest.json");info.size=len(manifest);tar.addfile(info,io.BytesIO(manifest))
        return archive

    def assert_rejected(self, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(ValueError):extract_and_verify(self.make_archive(root,**kwargs),root/"out")

    def test_valid_archive_is_fully_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);result=extract_and_verify(self.make_archive(root),root/"out")
            self.assertTrue(result["passed"]);self.assertEqual(2,result["files_verified"])

    def test_tampered_inner_hash_is_rejected(self):self.assert_rejected(bad_hash=True)
    def test_missing_evidence_is_rejected(self):self.assert_rejected(missing=True)
    def test_unexpected_evidence_is_rejected(self):self.assert_rejected(extra=True)
    def test_tar_traversal_is_rejected(self):self.assert_rejected(traversal=True)


if __name__=="__main__":unittest.main()
