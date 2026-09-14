import hashlib
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
import parser_client

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

    def test_client_rejects_truncated_worker_response(self):
        class FakeSocket:
            def settimeout(self,*_):pass
            def connect(self,*_):pass
            def sendall(self,*_):pass
            def recv(self,*_):return b''
            def __enter__(self):return self
            def __exit__(self,*_):pass
        fake=FakeSocket()
        with patch.object(parser_client.socket,'socket',return_value=fake),patch.dict('os.environ',{'PARSER_SOCKET_PATH':'/run/parser.sock'}):
            with self.assertRaises(ConnectionError):parser_client.extract(b'data','text/plain')

    def test_worker_rejects_over_limit_before_reading_payload(self):
        with self.assertRaisesRegex(ValueError,'metadata'):
            WORKER.process({'contract_version':'gcor.parser.v1','source_sha256':'a'*64,
                            'source_size':WORKER.MAX_INPUT+1,'declared_media_type':'text/plain'},b'')

    def test_worker_connection_timeout_is_bounded(self):
        self.assertGreater(WORKER.CONNECTION_TIMEOUT, 0)
        self.assertLessEqual(WORKER.CONNECTION_TIMEOUT, 60)

    def test_socket_write_failure_does_not_escape_connection_handler(self):
        class DisconnectedClient:
            timeout = None
            def settimeout(self, value): self.timeout = value
            def recv(self, _): return b''
            def sendall(self, _): raise BrokenPipeError("client left")
        client = DisconnectedClient()
        WORKER._handle_connection(client)
        self.assertEqual(WORKER.CONNECTION_TIMEOUT, client.timeout)

    def test_serialized_response_limit_fails_closed(self):
        with patch.object(WORKER, 'MAX_OUTPUT', 256):
            payload = WORKER._response_payload({'status':'complete','text':'x' * 1024})
        result = __import__('json').loads(payload)
        self.assertEqual('failed', result['status'])
        self.assertIn('exceeds output limit', result['error'])
