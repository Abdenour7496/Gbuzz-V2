"""Bound API admission and bodies before parsing or performing side effects."""
import asyncio

from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app, max_body_bytes: int, max_in_flight: int, body_timeout: float):
        if min(max_body_bytes, max_in_flight, body_timeout) <= 0:
            raise ValueError("Request limits must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_in_flight = max_in_flight
        self.body_timeout = body_timeout
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        if self.active >= self.max_in_flight:
            return await JSONResponse({"detail": "API capacity reached; retry later"}, 429,
                                      headers={"Retry-After": "2"})(scope, receive, send)
        # No await between checking and reserving: admission is atomic per event loop.
        self.active += 1
        try:
            lengths = [v for k, v in scope.get("headers", []) if k.lower() == b"content-length"]
            if lengths:
                try:
                    if len(lengths) != 1 or not lengths[0].isdigit():
                        raise ValueError
                    declared = int(lengths[0])
                except ValueError:
                    return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(scope, receive, send)
                if declared > self.max_body_bytes:
                    return await JSONResponse({"detail": "Request body exceeds configured limit"}, 413)(scope, receive, send)
            body = bytearray()
            size = 0
            try:
                async with asyncio.timeout(self.body_timeout):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        size += len(message.get("body", b""))
                        if size > self.max_body_bytes:
                            return await JSONResponse({"detail": "Request body exceeds configured limit"}, 413)(scope, receive, send)
                        body.extend(message.get("body", b""))
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await JSONResponse({"detail": "Request body timed out"}, 408)(scope, receive, send)

            replayed = False
            async def replay():
                nonlocal replayed
                if replayed:
                    return await receive()
                replayed = True
                message = {"type": "http.request", "body": bytes(body), "more_body": False}
                body.clear()
                return message

            await self.app(scope, replay, send)
        finally:
            self.active -= 1
