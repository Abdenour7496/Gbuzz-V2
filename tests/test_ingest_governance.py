"""Regression tests: ingest callers cannot set governance state; workload tokens can be channel-bound."""
import hashlib
import json
import unittest

import httpx

import main
from access_policy import ScopedAccess, current_principal


class IngestGovernanceTest(unittest.TestCase):
    def test_lifecycle_and_authority_fields_are_stripped(self):
        cleaned, removed = main.strip_governance_metadata({
            'record_type': 'buzz_event', 'knowledge_state': 'approved', 'knowledge_owner': 'ab' * 32,
            'knowledge_readers': None, 'knowledge_approved_by': 'x', 'superseded_by_document_id': 'y',
            'derivatives_invalidated': False, 'projected': True})
        self.assertEqual(cleaned, {'record_type': 'buzz_event', 'projected': True})
        self.assertIn('knowledge_state', removed)
        self.assertIn('superseded_by_document_id', removed)

    def test_http_ingest_handler_strips_before_storage(self):
        source = open(main.__file__).read()
        handler = source[source.index('async def ingest_document('):]
        handler = handler[:handler.index('\n@app.')]
        self.assertIn('strip_governance_metadata(parse_metadata_json(metadata_json))', handler)


class WorkloadChannelBindingTest(unittest.IsolatedAsyncioTestCase):
    async def test_bound_token_is_refused_outside_its_channels(self):
        seen = []

        async def endpoint(scope, receive, send):
            seen.append(current_principal.get())
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'{}'})
        token = 'bound-projector-token'
        allowed, other = '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'
        config = json.dumps([{'sha256': hashlib.sha256(token.encode()).hexdigest(), 'subject': 'projector',
                              'operations': ['ingest'], 'channels': [allowed]}])
        app = ScopedAccess(endpoint, mode='legacy', workloads=config)
        auth = {'X-Gcor-Workload-Authorization': f'Bearer {token}', 'X-Gcor-Access-Level': 'private'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            self.assertEqual((await client.post('/api/ingest', headers=auth | {'X-Gcor-Channel-Id': allowed})).status_code, 200)
            self.assertEqual((await client.post('/api/ingest', headers=auth | {'X-Gcor-Channel-Id': other})).status_code, 403)
        self.assertEqual(len(seen), 1)

    def test_unbound_token_configuration_remains_valid(self):
        config = json.dumps([{'sha256': 'a' * 64, 'subject': 'projector', 'operations': ['ingest']}])
        ScopedAccess(None, mode='legacy', workloads=config)

    def test_empty_channel_list_is_rejected(self):
        config = json.dumps([{'sha256': 'a' * 64, 'subject': 'projector', 'operations': ['ingest'], 'channels': []}])
        with self.assertRaises(ValueError):
            ScopedAccess(None, mode='legacy', workloads=config)


if __name__ == '__main__':
    unittest.main()
