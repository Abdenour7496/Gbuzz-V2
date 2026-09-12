import hashlib
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('parser_worker',Path(__file__).parents[1]/'parser-worker'/'worker.py')
WORKER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(WORKER)


class ParserWorkerTest(unittest.TestCase):
    def test_digest_bound_structured_output(self):
        body=b'Martin Bottos,78 hours\n';digest=hashlib.sha256(body).hexdigest()
        with patch.object(Path,'read_bytes',return_value=body):
            result=WORKER.process({'contract_version':'gcor.parser.v1','source_sha256':digest,'source_size':len(body),'declared_media_type':'text/csv'},body)
        self.assertEqual('complete',result['status'])
        self.assertEqual(digest,result['source_sha256'])
        self.assertTrue(result['anchors'])

    def test_digest_mismatch_fails_closed(self):
        with patch.object(Path,'read_bytes',return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError,'mismatch'):
                WORKER.process({'contract_version':'gcor.parser.v1','source_sha256':'a'*64,'source_size':5,'declared_media_type':'text/plain'})

    def test_contract_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'contract'):
            WORKER.process({'contract_version':'future'})
