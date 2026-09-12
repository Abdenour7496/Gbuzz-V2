"""Fail-closed, read-only service credentials for a bounded enterprise pilot.

This is a service authorization boundary, not an employee SSO implementation.
Only credential hashes are configured; caller-provided scope never grants access.
"""
import asyncio
import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass

from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Principal:
    subject: str
    channel_id: str
    access_level: str
    agent_id: str | None = None
    role: str = 'reader'


WORKSPACE_PATHS = {'/api/workspace/channels', '/api/workspace/documents', '/api/workspace/detail',
                   '/api/workspace/review', '/api/workspace/feedback', '/api/workspace/jobs',
                   '/api/workspace/submit', '/api/workspace/job-action', '/api/audit-packs'}
READ_PATHS = {'/api/ask', '/api/ask/reply', '/api/retrieve'}


current_principal: ContextVar[Principal | None] = ContextVar("gcor_principal", default=None)


def load_credentials(raw: str) -> dict[str, Principal]:
    entries = json.loads(raw)
    if not isinstance(entries, list):
        raise ValueError("GCOR_SCOPED_CREDENTIALS must be a JSON array")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"sha256", "subject", "channel_id", "access_level", "agent_id"}:
            raise ValueError("Invalid scoped credential fields")
        digest = entry.get("sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Credential sha256 must be a lowercase SHA-256 digest")
        if digest in result:
            raise ValueError("Duplicate credential digest")
        for key in ("subject", "channel_id", "access_level"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"Credential requires {key}")
        if entry.get("agent_id") is not None and (not isinstance(entry["agent_id"], str) or not entry["agent_id"].strip()):
            raise ValueError("Invalid agent_id")
        result[digest] = Principal(entry["subject"], entry["channel_id"], entry["access_level"], entry.get("agent_id"))
    return result


class ScopedAccess:
    def __init__(self, app, *, credentials: str = "[]", mode: str = "legacy", nostr=None):
        self.app = app
        if mode not in {"legacy", "scoped", "buzz"}:
            raise ValueError("GCOR_ACCESS_MODE must be legacy, scoped or buzz")
        self.credentials = load_credentials(credentials)
        self.mode = mode
        self.nostr = nostr
        if mode == "buzz" and nostr is None:
            raise ValueError("Buzz mode requires Nostr configuration")
        if mode == "scoped" and not self.credentials:
            raise ValueError("Scoped mode requires configured credentials")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        # Only health probes are public in scoped mode; docs/metrics are denied.
        if path in {"/health", "/health/live", "/health/ready", "/workspace", "/workspace.js", "/buzz-knowledge.mjs"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization")
        principal = None
        if authorization is not None:
            scheme, _, token = authorization.partition(b" ")
            if self.mode != "buzz" and scheme.lower() == b"bearer" and token and len(token) <= 16384:
                principal = self.credentials.get(hashlib.sha256(token).hexdigest())
            elif scheme.lower() == b"nostr" and token and len(token) <= 16384 and self.nostr is not None:
                if scope["method"] != "POST" or path not in READ_PATHS | WORKSPACE_PATHS:
                    return await JSONResponse({"detail": "Read-only identity endpoint"}, status_code=403)(scope, receive, send)
                try:
                    body = bytearray()
                    async with asyncio.timeout(15):
                        while True:
                            message = await receive()
                            if message["type"] == "http.disconnect":
                                return
                            body.extend(message.get("body", b""))
                            if len(body) > 1024 * 1024:
                                return await JSONResponse({"detail": "Identity request too large"}, status_code=413)(scope, receive, send)
                            if not message.get("more_body", False):
                                break
                    raw_body = bytes(body)
                    principal = await self.nostr.authenticate(token, scope, raw_body)
                    original_receive = receive
                    replayed = False
                    async def replay():
                        nonlocal replayed
                        if replayed:
                            return await original_receive()
                        replayed = True
                        return {"type": "http.request", "body": raw_body, "more_body": False}
                    receive = replay
                except PermissionError:
                    return await JSONResponse({"detail": "Authorization proof rejected or membership inactive"}, status_code=403)(scope, receive, send)
                except Exception:
                    principal = None
            if principal is None:
                return await JSONResponse({"detail": "Invalid scoped credential"}, status_code=401)(scope, receive, send)
        elif self.mode in {"scoped", "buzz"}:
            return await JSONResponse({"detail": "Scoped credential required"}, status_code=401)(scope, receive, send)
        permitted = READ_PATHS | WORKSPACE_PATHS if self.mode == 'buzz' else READ_PATHS
        if principal is not None and (scope["method"] != "POST" or path not in permitted):
            return await JSONResponse({"detail": "Read-only credential cannot access this endpoint"}, status_code=403)(scope, receive, send)
        reset = current_principal.set(principal)
        try:
            if principal is not None and self.mode == 'buzz':
                released = blocked = False
                async def release_checked(message):
                    nonlocal released, blocked
                    if blocked:
                        return
                    if message['type'] == 'http.response.start' and not released:
                        released = True
                        if not await self.nostr.still_authorized(principal):
                            blocked = True
                            return await JSONResponse({"detail": "Membership revoked before response release"}, status_code=403)(scope, receive, send)
                    await send(message)
                await self.app(scope, receive, release_checked)
            else:
                await self.app(scope, receive, send)
        finally:
            current_principal.reset(reset)
