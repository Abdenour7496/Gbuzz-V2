"""Bound API admission and bodies before parsing or performing side effects."""
import asyncio
import collections

from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app, max_body_bytes: int, max_in_flight: int, body_timeout: float,
                 max_queued: int = 0, queue_timeout: float = 0.0):
        if min(max_body_bytes, max_in_flight, body_timeout) <= 0:
            raise ValueError("Request limits must be positive")
        if max_queued < 0 or queue_timeout < 0:
            raise ValueError("Queue limits must not be negative")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_in_flight = max_in_flight
        self.body_timeout = body_timeout
        # Bounded FIFO wait instead of an immediate 429: short bursts above max_in_flight are
        # served in order; only a full queue or a wait longer than queue_timeout is rejected.
        self.max_queued = max_queued
        self.queue_timeout = queue_timeout
        self.waiters: collections.deque[asyncio.Future] = collections.deque()
        self.active = 0

    async def _admit(self) -> bool:
        if self.active < self.max_in_flight and not self.waiters:
            self.active += 1
            return True
        if len(self.waiters) >= self.max_queued or self.queue_timeout <= 0:
            return False
        waiter = asyncio.get_running_loop().create_future()
        self.waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, self.queue_timeout)
            return True  # the releasing request handed its slot over (active unchanged)
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                self._release()  # slot was handed over as the client went away: pass it on
            raise
        finally:
            try:
                self.waiters.remove(waiter)
            except ValueError:
                pass

    def _release(self) -> None:
        while self.waiters:
            waiter = self.waiters.popleft()
            if not waiter.done():
                waiter.set_result(True)  # hand the slot straight to the next queued request
                return
        self.active -= 1

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        if not await self._admit():
            return await JSONResponse({"detail": "API capacity reached; retry later"}, 429,
                                      headers={"Retry-After": "2"})(scope, receive, send)
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
            self._release()
