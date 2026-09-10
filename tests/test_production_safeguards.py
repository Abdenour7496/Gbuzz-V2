import asyncio
import ipaddress
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI, Request, UploadFile
from pydantic import ValidationError

import main
from request_limits import RequestLimits


class RequestLimitsTest(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, chunks, headers=(), limit=4, app=None):
        called = []
        async def downstream(scope, receive, send):
            called.append(await receive())
        middleware = RequestLimits(app or downstream, limit, 1, 0.02)
        messages = iter([{"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
                         for i, chunk in enumerate(chunks)])
        receive = AsyncMock(side_effect=lambda: next(messages))
        send = AsyncMock()
        await middleware({"type": "http", "path": "/api/ingest", "headers": headers}, receive, send)
        return called, send, middleware

    async def test_chunked_body_rejected_before_handler(self):
        called, send, middleware = await self.run_request([b"123", b"45"])
        self.assertEqual([], called)
        self.assertEqual(413, send.call_args_list[0].args[0]["status"])
        self.assertEqual(0, middleware.active)

    async def test_false_content_length_cannot_bypass_limit(self):
        called, send, _ = await self.run_request([b"12345"], [(b"content-length", b"1")])
        self.assertEqual([], called)
        self.assertEqual(413, send.call_args_list[0].args[0]["status"])

    async def test_exact_limit_reaches_handler_intact(self):
        called, _, _ = await self.run_request([b"12", b"34"])
        self.assertEqual(b"1234", called[0]["body"])

    async def test_invalid_length_is_rejected(self):
        for value in (b"-1", b"invalid"):
            called, send, _ = await self.run_request([], [(b"content-length", value)])
            self.assertEqual([], called)
            self.assertEqual(400, send.call_args_list[0].args[0]["status"])

    async def test_slow_body_times_out_without_starting_work(self):
        app = AsyncMock()
        middleware = RequestLimits(app, 4, 1, 0.01)
        async def receive():
            await asyncio.sleep(1)
        send = AsyncMock()
        await middleware({"type": "http", "path": "/api/ingest"}, receive, send)
        self.assertEqual(408, send.call_args_list[0].args[0]["status"])
        app.assert_not_called()
        self.assertEqual(0, middleware.active)

    async def test_capacity_rejects_work_but_keeps_health_available(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def downstream(scope, receive, send):
            if scope["path"] == "/api/ingest":
                entered.set()
                await release.wait()
        middleware = RequestLimits(downstream, 4, 1, 1)
        receive = AsyncMock(return_value={"type": "http.request", "body": b"", "more_body": False})
        scope = {"type": "http", "path": "/api/ingest"}
        task = asyncio.create_task(middleware(scope, receive, AsyncMock()))
        try:
            await entered.wait()
            send = AsyncMock()
            await middleware(scope, receive, send)
            self.assertEqual(429, send.call_args_list[0].args[0]["status"])
            await middleware({"type": "http", "path": "/health/live"}, receive, AsyncMock())
        finally:
            release.set()
            await task
        self.assertEqual(0, middleware.active)

    async def test_handler_failure_releases_capacity(self):
        app = AsyncMock(side_effect=RuntimeError("failure"))
        middleware = RequestLimits(app, 4, 1, 1)
        with self.assertRaises(RuntimeError):
            await middleware({"type": "http", "path": "/api/ingest"},
                             AsyncMock(return_value={"type": "http.request", "body": b""}), AsyncMock())
        self.assertEqual(0, middleware.active)


class ProductionEndpointsTest(unittest.IsolatedAsyncioTestCase):
    async def test_remote_total_deadline_stops_a_slow_fetch(self):
        async def slow(*args):
            await asyncio.sleep(1)
        with patch.object(main, "REMOTE_FETCH_TOTAL_TIMEOUT_SECONDS", 0.01), \
                patch.object(main, "stream_remote_file", slow):
            with self.assertRaises(httpx.ReadTimeout):
                await main.download_remote_file("http://test/file", 60)

    async def test_exhausted_attachment_budget_does_not_start_fetch(self):
        budget = main.AttachmentBudget()
        budget.remaining_bytes = 0
        with patch.object(main, "stream_remote_file", AsyncMock()) as stream:
            with self.assertRaises(main.HTTPException):
                await main.fetch_remote_file_with_retries("http://test/file", budget=budget)
        stream.assert_not_called()

    async def test_attachment_budget_is_shared_across_downloads(self):
        client_class = httpx.AsyncClient
        budget = main.AttachmentBudget()
        budget.remaining_bytes = 5
        def client(**kwargs):
            kwargs.pop("transport", None)
            return client_class(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"123")), **kwargs)
        with patch.object(main.httpx, "AsyncClient", side_effect=client), \
                patch.object(main, "resolve_host_addresses", AsyncMock(return_value=[ipaddress.ip_address("8.8.8.8")])), \
                patch.object(main, "REMOTE_FETCH_ALLOWED_HOSTS", ["allowed.test"]):
            await main.download_remote_file("https://allowed.test/file", 1, budget)
            with self.assertRaises(main.HTTPException) as error:
                await main.download_remote_file("https://allowed.test/file", 1, budget)
        self.assertEqual(413, error.exception.status_code)

    async def test_session_and_explicit_attachment_count_is_combined_before_writes(self):
        session = main.sample_chat_session()
        session["messages"][0]["attachments"] = [{"url": "http://test/a"}]
        with patch.object(main, "MAX_ATTACHMENTS_PER_REQUEST", 1), \
                patch.object(main, "validate_chat_session_record", return_value=session), \
                patch.object(main, "verify_stack_api_secret"), patch.object(main, "verify_webhook"), \
                patch.object(main, "ensure_bucket_name", AsyncMock()) as bucket:
            with self.assertRaises(main.HTTPException) as error:
                await main.ingest_document(SimpleNamespace(), session_json="{}", attachments_json='[{"url":"http://test/b"}]')
        self.assertEqual(422, error.exception.status_code)
        bucket.assert_not_called()

    async def test_query_limit_is_validated(self):
        for model in (main.AskRequest, main.RetrieveRequest):
            with self.assertRaises(ValidationError):
                model(query="x" * (main.MAX_QUERY_CHARS + 1))

    async def test_readiness_reports_storage_failure_without_leaking_error(self):
        state = SimpleNamespace(pool=SimpleNamespace(fetchval=AsyncMock(return_value=1)),
                                readiness_s3=SimpleNamespace(head_bucket=Mock(side_effect=RuntimeError("secret endpoint"))))
        self.assertEqual({"postgres": "ok", "object_storage": "unavailable"},
                         await main.check_readiness_dependencies(state))

    async def test_readiness_failure_returns_503_and_reuses_probe(self):
        state = SimpleNamespace(readiness_task=None, readiness_checked_at=0)
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        with patch.object(main, "check_readiness_dependencies", AsyncMock(return_value={"postgres": "unavailable"})) as check:
            self.assertEqual(503, (await main.readiness(request)).status_code)
            self.assertEqual(503, (await main.readiness(request)).status_code)
            self.assertEqual(1, check.await_count)

    async def test_upload_read_is_bounded_before_ingestion(self):
        file = Mock(spec=UploadFile)
        file.read = AsyncMock(return_value=b"12345")
        with patch.object(main, "MAX_INGEST_FILE_BYTES", 4), \
                patch.object(main, "verify_stack_api_secret"), patch.object(main, "verify_webhook"), \
                patch.object(main, "ingest_payload", AsyncMock()) as ingest:
            with self.assertRaises(main.HTTPException) as error:
                await main.ingest_document(SimpleNamespace(), file=file)
        self.assertEqual(413, error.exception.status_code)
        file.read.assert_awaited_once_with(5)
        ingest.assert_not_called()

    async def test_real_fastapi_parser_gets_413_for_oversize_form(self):
        app = FastAPI()
        called = []
        @app.post("/api/ingest")
        async def ingest(request: Request):
            called.append(True)
            return dict(await request.form())
        app.add_middleware(RequestLimits, max_body_bytes=4, max_in_flight=1, body_timeout=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/api/ingest", data={"text": "oversized"})
        self.assertEqual(413, response.status_code)
        self.assertEqual([], called)
