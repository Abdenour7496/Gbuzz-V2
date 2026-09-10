import io
import unittest
import zipfile
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace
from uuid import uuid4
from fastapi import HTTPException
from access_policy import Principal,current_principal
from enterprise_workflows import identity,submit,Submission,process_one
from document_parsing import extract


class Parsing(unittest.TestCase):
    def test_binary_and_invalid_utf8_are_rejected(self):
        for body,kind in [(b'\xff','text/plain'),(b'PK\x00','application/octet-stream'),(b'x','application/msword')]:
            with self.assertRaises(HTTPException): extract(body,kind)

    def test_docx_paragraphs_and_table_cells(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('word/document.xml','<document><p><t>Policy</t></p><table><p><t>Value</t></p></table></document>')
        self.assertIn('Policy\nValue',extract(output.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))

    def test_xml_entity_declarations_are_rejected(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('word/document.xml','<!DOCTYPE x [<!ENTITY x "expanded">]><p>&x;</p>')
        with self.assertRaises(HTTPException):extract(output.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')


class WorkflowRoles(unittest.IsolatedAsyncioTestCase):
    def test_member_cannot_approve_guest_cannot_submit(self):
        for role,options in [('member',{'admin':True}),('guest',{'contributor':True}),('bot',{'contributor':True})]:
            reset=current_principal.set(Principal('a','b','private',role=role))
            try:
                with self.assertRaises(HTTPException):identity(**options)
            finally:current_principal.reset(reset)

    async def test_submission_rejects_reused_key_for_different_payload(self):
        reset=current_principal.set(Principal('a','b','public',role='member'))
        try:
            connection=MagicMock()
            connection.execute=AsyncMock()
            connection.fetchrow=AsyncMock(return_value={'request_hash':'wrong'})
            pool=MagicMock();pool.acquire.return_value.__aenter__.return_value=connection
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool)))
            with self.assertRaises(HTTPException) as error:
                await submit(Submission(channel_id=uuid4(),request_id=uuid4(),title='title',text='body'),request)
            self.assertEqual(error.exception.status_code,409)
        finally:current_principal.reset(reset)

    async def test_revoked_submitter_fails_without_ingestion(self):
        row={'id':uuid4(),'channel_id':str(uuid4()),'actor':'01'*32,'attempts':1,'payload':{'access_level':'public'}}
        pool=SimpleNamespace(fetchrow=AsyncMock(return_value=row),fetchval=AsyncMock(return_value=None),execute=AsyncMock())
        with patch('main.ingest_payload',AsyncMock()) as ingest:
            await process_one(SimpleNamespace(state=SimpleNamespace(pool=pool)))
            ingest.assert_not_awaited()
        self.assertEqual(pool.execute.call_args.args[3],'failed')
