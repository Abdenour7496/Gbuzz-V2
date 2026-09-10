"""HTTP transport that resolves and validates the actual TCP destination."""
import ipaddress
from contextlib import contextmanager

import httpcore
import httpx


def is_public_address(address):
    # Transition mechanisms can embed destinations that differ from the visible IP.
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_public_address(address.ipv4_mapped)
        if any(address in network for network in (
            ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"),
            ipaddress.ip_network("2002::/16"), ipaddress.ip_network("2001::/32"),
        )):
            return False
    return address.is_global and not address.is_multicast


class ValidatedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, resolve, block_private=True, backend=None):
        self.resolve = resolve
        self.block_private = block_private
        self.backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = await self.resolve(host)
        if not addresses or (self.block_private and any(not is_public_address(ip) for ip in addresses)):
            # Policy errors must not become retryable network failures.
            from fastapi import HTTPException
            raise HTTPException(422, "Remote destination is blocked by outbound policy")
        last_error = None
        for address in addresses:
            try:
                # Only a checked numeric IP reaches the socket backend. HTTP Host and
                # TLS SNI/certificate verification retain the original URL hostname.
                return await self.backend.connect_tcp(str(address), port, timeout=timeout,
                                                      local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as error:
                last_error = error
        raise last_error


@contextmanager
def map_transport_errors():
    try:
        yield
    except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError,
            httpcore.ProxyError, httpcore.UnsupportedProtocol) as error:
        error_type = getattr(httpx, type(error).__name__, httpx.TransportError)
        raise error_type(str(error)) from error


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        with map_transport_errors():
            async for chunk in self.stream:
                yield chunk

    async def aclose(self):
        await self.stream.aclose()


class ValidatedTransport(httpx.AsyncBaseTransport):
    def __init__(self, resolve, block_private=True, backend=None):
        self.pool = httpcore.AsyncConnectionPool(
            network_backend=ValidatedBackend(resolve, block_private, backend),
            max_connections=4, max_keepalive_connections=0,
        )

    async def handle_async_request(self, request):
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        )
        with map_transport_errors():
            response = await self.pool.handle_async_request(core_request)
        return httpx.Response(response.status, headers=response.headers,
                              stream=ResponseStream(response.stream), extensions=response.extensions)

    async def aclose(self):
        await self.pool.aclose()
