import hashlib
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import base64
from coincurve import PrivateKey

import main
from access_policy import Principal, ScopedAccess, current_principal
from nostr_auth import BuzzIdentity, verify_event


TOKEN = "test-credential-with-no-production-authority"
CONFIG = json.dumps([{"sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
                      "subject": "test-reader", "channel_id": "dept-a", "access_level": "public"}])


class AccessTest(unittest.IsolatedAsyncioTestCase):
    async def test_workload_identity_is_channel_and_operation_bound(self):
        seen=[]
        async def endpoint(scope,receive,send):
            seen.append(current_principal.get())
            await send({'type':'http.response.start','status':200,'headers':[]});await send({'type':'http.response.body','body':b'{}'})
        token='projector-token'
        config=json.dumps([{'sha256':hashlib.sha256(token.encode()).hexdigest(),'subject':'projector','operations':['ingest']}])
        app=ScopedAccess(endpoint,mode='legacy',workloads=config)
        auth={'X-Gcor-Workload-Authorization':f'Bearer {token}'}
        good=auth|{'X-Gcor-Channel-Id':'11111111-1111-1111-1111-111111111111'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            self.assertEqual((await client.post('/api/ingest',headers=good)).status_code,200)
            self.assertEqual((await client.post('/api/retrieve',headers=good)).status_code,403)
            self.assertEqual((await client.post('/api/ingest',headers=auth)).status_code,403)
        self.assertTrue(seen[0].workload)
        self.assertEqual(seen[0].channel_id,'11111111-1111-1111-1111-111111111111')
    async def test_fail_closed_routes_and_context(self):
        seen = []
        async def endpoint(scope, receive, send):
            seen.append(current_principal.get())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})
        app = ScopedAccess(endpoint, credentials=CONFIG, mode="scoped")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            self.assertEqual((await client.post('/api/ask', headers={"X-Gcor-Webhook-Secret": "anything"})).status_code, 401)
            headers = {"Authorization": f"Bearer {TOKEN}"}
            for path in ('/api/sessions', '/api/sessions/one', '/api/recovery/records', '/api/knowledge/approve', '/metrics', '/docs'):
                self.assertEqual((await client.post(path, headers=headers)).status_code, 403)
            self.assertEqual((await client.post('/api/ask', headers=headers)).status_code, 200)
            self.assertEqual(seen[-1].channel_id, 'dept-a')
            self.assertIsNone(current_principal.get())

    async def test_invalid_bearer_never_falls_back_to_legacy(self):
        endpoint = AsyncMock()
        app = ScopedAccess(endpoint, credentials=CONFIG, mode='legacy')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/api/ask', headers={'Authorization': 'Bearer bad', 'X-Gcor-Webhook-Secret': 'valid'})
        self.assertEqual(response.status_code, 401)
        endpoint.assert_not_awaited()

    async def test_unavailable_buzz_fails_closed(self):
        app = ScopedAccess(AsyncMock(), mode='buzz', nostr=SimpleNamespace(authenticate=AsyncMock(side_effect=TimeoutError)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            self.assertEqual((await client.post('/api/ask', headers={'Authorization':'Nostr token'})).status_code, 401)

    async def test_scope_cannot_be_overridden(self):
        reset = current_principal.set(Principal('reader', 'dept-a', 'public'))
        try:
            with patch.object(main, 'embed', AsyncMock()) as embed:
                with self.assertRaises(main.HTTPException) as error:
                    await main.run_retrieval_query(None, query='q', top_k=1, hops=1, access_level='public',
                        min_confidence=0, agent_id=None, vector_weight=1, lexical_weight=0, channel_id='dept-b')
                self.assertEqual(error.exception.status_code,403)
                embed.assert_not_awaited()
        finally:
            current_principal.reset(reset)

    def test_bad_configuration_is_rejected(self):
        for mode, config in [('scoped','[]'), ('typo',CONFIG), ('legacy','{}')]:
            with self.assertRaises(ValueError):
                ScopedAccess(None,mode=mode,credentials=config)


def signed_event(key, body=b'{}', url='http://test/api/ask', **changes):
    event={'pubkey':key.public_key_xonly.format().hex(),'created_at':int(time.time()),'kind':27235,
           'content':'','tags':[['u',url],['method','POST'],['payload',hashlib.sha256(body).hexdigest()]]}
    event.update(changes)
    raw=json.dumps([0,event['pubkey'],event['created_at'],event['kind'],event['tags'],event['content']],ensure_ascii=False,separators=(',',':')).encode()
    digest=hashlib.sha256(raw).digest()
    event.update(id=digest.hex(),sig=key.sign_schnorr(digest).hex())
    return base64.b64encode(json.dumps(event).encode())


class NostrTest(unittest.IsolatedAsyncioTestCase):
    def test_signature_and_request_binding(self):
        key=PrivateKey()
        token=signed_event(key)
        self.assertEqual(verify_event(token,'http://test/api/ask','POST',b'{}'),key.public_key_xonly.format().hex())
        for url,method,body in [('http://evil/api/ask','POST',b'{}'),('http://test/api/ask','GET',b'{}'),('http://test/api/ask','POST',b'{"channel_id":"other"}')]:
            with self.subTest(url=url,method=method,body=body), self.assertRaises(ValueError):
                verify_event(token,url,method,body)
        for changes in ({'created_at':int(time.time())-61},{'created_at':int(time.time())+61},{'kind':1},{'content':'grant access'},
                        {'tags':[['u','http://test/api/ask'],['u','http://test/api/ask']]}, {'pubkey':PrivateKey().public_key_xonly.format().hex()}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                verify_event(signed_event(key,**changes),'http://test/api/ask','POST',b'{}')

    def test_tampered_signature_rejected(self):
        event=json.loads(base64.b64decode(signed_event(PrivateKey())))
        event['sig']='00'*64
        with self.assertRaises(ValueError):
            verify_event(base64.b64encode(json.dumps(event).encode()),'http://test/api/ask','POST',b'{}')

    async def test_membership_revocation_and_private_classification(self):
        pool=SimpleNamespace(fetchrow=AsyncMock(side_effect=[{'visibility':'private'},None]))
        auth=BuzzIdentity(SimpleNamespace(state=SimpleNamespace(pool=pool)),'http://test')
        body=b'{"channel_id":"11111111-1111-1111-1111-111111111111"}'
        token=signed_event(PrivateKey(),body)
        scope={'path':'/api/ask','method':'POST'}
        self.assertEqual((await auth.authenticate(token,scope,body)).access_level,'private')
        with self.assertRaises(PermissionError):
            await auth.authenticate(signed_event(PrivateKey(),body),scope,body)
        self.assertEqual(pool.fetchrow.await_count,2)

    async def test_nip98_proof_is_single_use(self):
        pool=SimpleNamespace(fetchrow=AsyncMock(return_value={'visibility':'private'}))
        auth=BuzzIdentity(SimpleNamespace(state=SimpleNamespace(pool=pool)),'http://test')
        body=b'{"channel_id":"11111111-1111-1111-1111-111111111111"}'
        token=signed_event(PrivateKey(),body)
        scope={'path':'/api/ask','method':'POST'}
        await auth.authenticate(token,scope,body)
        with self.assertRaisesRegex(PermissionError,'already used'):
            await auth.authenticate(token,scope,body)

    async def test_response_release_rechecks_membership(self):
        async def endpoint(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'{}'})
        principal=Principal('a'*64,'11111111-1111-1111-1111-111111111111','private')
        nostr=SimpleNamespace(authenticate=AsyncMock(return_value=principal),still_authorized=AsyncMock(return_value=False))
        app=ScopedAccess(endpoint,mode='buzz',nostr=nostr)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            response=await client.post('/api/ask',content=b'{}',headers={'Authorization':'Nostr token'})
        self.assertEqual(response.status_code,403)


class EvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_or_missing_citations_use_excerpts(self):
        matches=[{'title':'Policy','content':'Supported statement.'}]
        for answer in ('Invented statement.', 'Invented [2]', 'Invented [0]'):
            response=Mock(json=Mock(return_value={'response':answer}),raise_for_status=Mock())
            client=AsyncMock()
            client.post.return_value=response
            with patch.object(main,'GENERATION_MODEL','test'), patch.object(main.httpx,'AsyncClient') as factory:
                factory.return_value.__aenter__.return_value=client
                result=await main.generate_grounded_answer('q',matches)
                self.assertIn('[1] Supported statement.',result)
                self.assertNotIn('Invented',result)

    async def test_answer_and_citations_share_same_evidence_limit(self):
        rows=[{'id':str(i),'node_id':str(i),'document_id':str(i),'title':str(i),
               'ordinal':i,'score':1,'content':str(i)} for i in range(8)]
        with patch.object(main,'verify_stack_api_secret'), patch.object(main,'run_retrieval_query',AsyncMock(return_value=(rows,[]))), \
             patch.object(main,'generate_grounded_answer',AsyncMock(return_value='answer [1]')) as generate:
            result=await main.ask(main.AskRequest(query='q',channel_id='a',max_citations=2),None)
        self.assertEqual(len(result['citations']),2)
        self.assertEqual(len(generate.call_args.args[1]),2)
