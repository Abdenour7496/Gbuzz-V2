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
import main


class Parsing(unittest.TestCase):
    def test_extraction_version_changes_document_identity(self):
        args=('a'*64,'private',None,'channel','Channel')
        self.assertEqual(main.document_identity(*args,'v1'),main.document_identity(*args,'v1'))
        self.assertNotEqual(main.document_identity(*args,'v1'),main.document_identity(*args,'v2'))

    def test_binary_and_invalid_utf8_are_rejected(self):
        for body,kind in [(b'\xff','text/plain'),(b'PK\x00','application/octet-stream'),(b'x','application/msword')]:
            with self.assertRaises(HTTPException): extract(body,kind)

    def test_docx_paragraphs_and_table_cells(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('word/document.xml','<document><p><t>Policy</t></p><table><p><t>Value</t></p></table></document>')
        text=extract(output.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        self.assertIn('[Paragraph 1]\nPolicy',text)
        self.assertIn('Value',text)

    def test_csv_cells_have_stable_anchors(self):
        text=extract(b'Consultant,Hours\nMartin Bottos,78 hours\n','text/csv')
        self.assertIn('[Sheet CSV Cell A2]\nMartin Bottos',text)
        self.assertIn('[Sheet CSV Cell B2]\n78 hours',text)

    def test_xlsx_cells_have_stable_sheet_anchors(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('xl/workbook.xml','<workbook xmlns:r="rel"><sheet name="Timesheet" r:id="rId1"/></workbook>')
            archive.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr('xl/sharedStrings.xml','<sst><si><t>Martin Bottos</t></si><si><t>78 hours</t></si></sst>')
            archive.writestr('xl/worksheets/sheet1.xml','<worksheet><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></worksheet>')
        text=extract(output.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('[Sheet Timesheet Cell A1]\nMartin Bottos',text)
        self.assertIn('[Sheet Timesheet Cell B1]\n78 hours',text)

    def test_xml_entity_declarations_are_rejected(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('word/document.xml','<!DOCTYPE x [<!ENTITY x "expanded">]><p>&x;</p>')
        with self.assertRaises(HTTPException):extract(output.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    def test_archive_expansion_limit_is_enforced(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('word/document.xml',b'x'*(50*1024*1024+1))
        with self.assertRaises(HTTPException):extract(output.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    def test_corrupt_and_truncated_office_files_fail_closed(self):
        for body in (b'not-a-zip',b'PK\x03\x04truncated'):
            with self.assertRaises(HTTPException):extract(body,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_missing_ocr_worker_fails_closed(self):
        with self.assertRaises(HTTPException):extract(b'image','image/png')


class AttachmentIdempotency(unittest.IsolatedAsyncioTestCase):
    async def test_completed_digest_version_replay_has_no_side_effects(self):
        existing = {
            'document_id': uuid4(), 'record_id': uuid4(), 'chunks': 2,
            'bucket': 'test', 'original_key': 'original',
            'markdown_key': 'content.md', 'record_key': 'record.json',
        }
        pool = SimpleNamespace(fetchrow=AsyncMock(return_value=existing))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool)))
        discovered_before = main.ATTACHMENT_STAGES.labels(stage='discovered')._value.get()
        with patch.object(main, 'extract_text') as extract_text, patch.object(main, 'embed') as embed:
            result = await main.ingest_payload(
                request, content=b'same attachment', media_type='text/plain', title='same.txt',
                access_level='private', agent_id=None, source_uri='buzz://event/test#attachment:1',
                channel_name='Test', channel_id=str(uuid4()), event_id='a' * 64,
                event_kind='buzz.kind.9', event_timestamp='2026-01-01T00:00:00Z',
                author_pubkey='b' * 64, file_url=None, file_name='same.txt',
                metadata={'record_type': 'buzz_attachment', 'extraction_version': 'v1'},
            )
        self.assertTrue(result['deduplicated'])
        self.assertTrue(result['idempotent_replay'])
        self.assertEqual(2, result['chunks'])
        self.assertEqual(discovered_before, main.ATTACHMENT_STAGES.labels(stage='discovered')._value.get())
        extract_text.assert_not_called()
        embed.assert_not_called()


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
