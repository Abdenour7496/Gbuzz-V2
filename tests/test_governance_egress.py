import ipaddress
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpcore
import httpx
from botocore.exceptions import ClientError
from fastapi import HTTPException

import main
from egress import ValidatedBackend, ValidatedTransport, is_public_address
from governance_outbox import build_event, publish_artifact
from governance_service import commit_governance


class EgressTest(unittest.IsolatedAsyncioTestCase):
    def test_special_destinations_are_not_public(self):
        for value in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "100.64.0.1", "0.0.0.0",
                      "224.0.0.1", "::1", "fc00::1", "::ffff:127.0.0.1", "64:ff9b::a00:1",
                      "2002:7f00:1::1", "2001::1"):
            with self.subTest(value=value):
                self.assertFalse(is_public_address(ipaddress.ip_address(value)))
        self.assertTrue(is_public_address(ipaddress.ip_address("8.8.8.8")))

    async def test_dns_rebinding_is_rechecked_at_connection(self):
        resolver = AsyncMock(side_effect=[[ipaddress.ip_address("8.8.8.8")], [ipaddress.ip_address("127.0.0.1")]])
        socket_backend = SimpleNamespace(connect_tcp=AsyncMock())
        with patch.object(main, "resolve_host_addresses", resolver), patch.object(main, "REMOTE_FETCH_ALLOWED_HOSTS", []):
            await main.validate_remote_fetch_url("https://allowed.test/file")
        backend = ValidatedBackend(resolver, backend=socket_backend)
        with self.assertRaises(HTTPException):
            await backend.connect_tcp("allowed.test", 443)
        socket_backend.connect_tcp.assert_not_called()

    async def test_mixed_dns_answers_fail_closed(self):
        socket_backend = SimpleNamespace(connect_tcp=AsyncMock())
        backend = ValidatedBackend(AsyncMock(return_value=[ipaddress.ip_address("8.8.8.8"), ipaddress.ip_address("10.0.0.1")]), backend=socket_backend)
        with self.assertRaises(HTTPException):
            await backend.connect_tcp("allowed.test", 443)
        socket_backend.connect_tcp.assert_not_called()

    async def test_real_httpcore_transport_pins_ip_and_retains_tls_hostname(self):
        captured = {}
        class Stream(httpcore.AsyncMockStream):
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                captured["hostname"] = server_hostname
                captured["verify"] = ssl_context.verify_mode
                captured["check_hostname"] = ssl_context.check_hostname
                return self
            async def write(self, buffer, timeout=None):
                captured.setdefault("request", bytearray()).extend(buffer)
        stream = Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"])
        backend = SimpleNamespace(connect_tcp=AsyncMock(return_value=stream))
        resolver = AsyncMock(return_value=[ipaddress.ip_address("8.8.8.8")])
        transport = ValidatedTransport(resolver, backend=backend)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            response = await client.get("https://allowed.test/file")
        self.assertEqual("ok", response.text)
        self.assertEqual("8.8.8.8", backend.connect_tcp.call_args.args[0])
        self.assertEqual("allowed.test", captured["hostname"])
        self.assertEqual(ssl.CERT_REQUIRED, captured["verify"])
        self.assertTrue(captured["check_hostname"])
        self.assertIn(b"Host: allowed.test", captured["request"])

    async def test_no_unsafe_fallback_after_checked_address_fails(self):
        backend = SimpleNamespace(connect_tcp=AsyncMock(side_effect=httpcore.ConnectError("failed")))
        transport = ValidatedTransport(AsyncMock(return_value=[ipaddress.ip_address("8.8.8.8")]), backend=backend)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            with self.assertRaises(httpx.ConnectError):
                await client.get("https://allowed.test/file")
        self.assertEqual(1, backend.connect_tcp.await_count)
        self.assertEqual("8.8.8.8", backend.connect_tcp.call_args.args[0])

    async def test_url_credentials_and_bad_ports_are_rejected(self):
        for url in ("https://user:password@allowed.test/file", "http://allowed.test:99999/", "http://[broken/"):
            with self.subTest(url=url), self.assertRaises(HTTPException):
                await main.validate_remote_fetch_url(url)


class GovernanceArtifactTest(unittest.TestCase):
    def row(self):
        doc_id = uuid4()
        event_id, bucket, key, payload = build_event(doc_id, "approved", {}, "reviewer", None, "archive")
        return {"event_id": event_id, "document_id": doc_id, "bucket": bucket, "object_key": key, "payload": payload}

    def test_retry_uses_identical_object_and_conditional_create(self):
        row = self.row()
        s3 = Mock()
        publish_artifact(s3, row)
        first = s3.put_object.call_args.kwargs
        s3.put_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        s3.head_object.return_value = {"Metadata": first["Metadata"]}
        publish_artifact(s3, row)
        self.assertEqual(first, s3.put_object.call_args.kwargs)
        self.assertEqual("*", first["IfNoneMatch"])

    def test_collision_is_not_acknowledged(self):
        s3 = Mock()
        s3.put_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        s3.head_object.return_value = {"Metadata": {"sha256": "different"}}
        with self.assertRaises(RuntimeError):
            publish_artifact(s3, self.row())

    def test_storage_failure_propagates_for_durable_retry(self):
        s3 = Mock()
        s3.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        with self.assertRaises(ClientError):
            publish_artifact(s3, self.row())


class GovernanceSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_migration_never_uses_legacy_write_path(self):
        pool = Mock()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(governance_available=False, pool=pool)))
        with self.assertRaises(HTTPException) as error:
            await commit_governance(request, uuid4(), {}, "approved", None, None, None, "hash", "approve", "bucket", AsyncMock())
        self.assertEqual(503, error.exception.status_code)
        pool.acquire.assert_not_called()
