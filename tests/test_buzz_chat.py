import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock
from coincurve import PrivateKey
from buzz_chat import sign_event, verified, principal, source_events, command_text


def event_row(key=None, content='!knowledge help', channel=None):
    key=key or PrivateKey();channel=channel or uuid4()
    event=sign_event(key,9,[['h',str(channel)]],content)
    return dict(event_id=event['id'],author=event['pubkey'],signature=event['sig'],
        created_at=datetime.fromtimestamp(event['created_at'],timezone.utc),channel_id=channel,
        kind=9,tags=event['tags'],content=content)


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def test_copied_inline_command_preserves_signed_content(self):
        row=event_row(content=' `!knowledge help` \n')
        event=verified(row)
        self.assertEqual(command_text(event['content']), '!knowledge help')
        self.assertEqual(event['content'], row['content'])
        self.assertEqual(command_text('!knowledge ask What is `x`?'), '!knowledge ask What is `x`?')
        self.assertEqual(command_text('Someone said `!knowledge help`'), 'Someone said `!knowledge help`')

    def test_signature_and_channel_binding(self):
        row=event_row();self.assertEqual(verified(row)['pubkey'],row['author'])
        for field,value in [('content','!knowledge approve forged'),('channel_id',uuid4()),('author',PrivateKey().public_key_xonly.format().hex())]:
            with self.assertRaises(ValueError):verified(row|{field:value})

    async def test_removed_member_denied(self):
        pool=AsyncMock();pool.fetchrow.return_value=None
        with self.assertRaises(PermissionError):await principal(pool,uuid4(),'aa'*32)

    async def test_agent_requires_sponsor(self):
        pool=AsyncMock();pool.fetchrow.return_value={'visibility':'private','role':'bot','agent_type':'agent','agent_owner_pubkey':b'a'*32}
        pool.fetchval.return_value=None
        with self.assertRaises(PermissionError):await principal(pool,uuid4(),'aa'*32)
        pool.fetchval.return_value=1
        p=await principal(pool,uuid4(),'aa'*32);self.assertEqual(p.agent_id,'aa'*32)

    async def test_missing_or_cross_channel_evidence_denied(self):
        pool=AsyncMock();pool.fetch.return_value=[]
        with self.assertRaises(Exception):await source_events(pool,str(uuid4()),['ab'*32])

    async def test_generated_reply_not_independent_evidence(self):
        pool=AsyncMock();row=event_row();pool.fetch.return_value=[row]
        with self.assertRaises(Exception):await source_events(pool,str(row['channel_id']),[row['event_id']])


class AgentAuthorityTests(unittest.IsolatedAsyncioTestCase):
    """Agents contribute knowledge; only humans establish it."""

    def app_for(self, role, agent_type):
        from types import SimpleNamespace
        pool=AsyncMock()
        pool.fetchrow.return_value={'visibility':'private','role':role,'agent_type':agent_type,'agent_owner_pubkey':b'a'*32}
        pool.fetchval.return_value=1  # sponsor active
        return SimpleNamespace(state=SimpleNamespace(pool=pool))

    async def test_sponsored_agent_cannot_review_even_with_owner_role(self):
        from fastapi import HTTPException
        from buzz_chat import execute
        for action in ('approve','reject','archive'):
            row=event_row(content=f'!knowledge {action} {uuid4()} 2026-10-10T00:00:00+00:00')
            with self.assertRaises(HTTPException) as error:
                await execute(self.app_for('owner','agent'),row)
            self.assertEqual(error.exception.status_code,403,action)

    async def test_human_owner_reaches_review(self):
        # The same command from a human owner passes the authority check (and
        # then fails later on the mocked document lookup, not with 403).
        from fastapi import HTTPException
        from buzz_chat import execute
        row=event_row(content=f'!knowledge approve {uuid4()} 2026-10-10T00:00:00+00:00')
        try:
            await execute(self.app_for('owner',None),row)
        except HTTPException as error:
            self.assertNotEqual(error.status_code,403)
        except Exception:
            pass
