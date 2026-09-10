"""Bind the active Buzz identity to the database connection.

While access_policy.current_principal holds a Principal, every connection handed out by
ScopedPool carries ``gcor.channel_id`` and ``gcor.access_level`` session settings. The
row-level security policies in migrations/0012_row_level_security.sql read them, so a
query that forgets an application filter still cannot see or write rows outside the
identity's channel. asyncpg resets session settings (``RESET ALL``) when a connection
returns to the pool, so scope never leaks between requests.

Without a principal (legacy shared-secret API, background workers) connections are
handed out untouched and the policies permit every row.
"""
from __future__ import annotations

from access_policy import current_principal

SCOPE_SQL = "SELECT set_config('gcor.channel_id', $1, false), set_config('gcor.access_level', $2, false)"


async def apply_scope(connection) -> None:
    principal = current_principal.get()
    if principal is not None:
        await connection.execute(SCOPE_SQL, principal.channel_id or '', principal.access_level or '')


class _ScopedAcquire:
    def __init__(self, pool):
        self._pool = pool
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._pool.acquire()
        connection = await self._ctx.__aenter__()
        try:
            await apply_scope(connection)
        except BaseException:
            await self._ctx.__aexit__(None, None, None)
            raise
        return connection

    async def __aexit__(self, *exc):
        return await self._ctx.__aexit__(*exc)

    def __await__(self):
        return self.__aenter__().__await__()


class ScopedPool:
    """asyncpg.Pool facade that tags connections with the caller's identity scope."""

    def __init__(self, pool):
        self._pool = pool

    def acquire(self, **kwargs):
        if kwargs:
            raise TypeError('ScopedPool.acquire does not accept options')
        return _ScopedAcquire(self._pool)

    async def release(self, connection):
        return await self._pool.release(connection)

    async def execute(self, query, *args, **kwargs):
        async with self.acquire() as connection:
            return await connection.execute(query, *args, **kwargs)

    async def executemany(self, query, args, **kwargs):
        async with self.acquire() as connection:
            return await connection.executemany(query, args, **kwargs)

    async def fetch(self, query, *args, **kwargs):
        async with self.acquire() as connection:
            return await connection.fetch(query, *args, **kwargs)

    async def fetchrow(self, query, *args, **kwargs):
        async with self.acquire() as connection:
            return await connection.fetchrow(query, *args, **kwargs)

    async def fetchval(self, query, *args, **kwargs):
        async with self.acquire() as connection:
            return await connection.fetchval(query, *args, **kwargs)

    async def close(self):
        return await self._pool.close()

    def terminate(self):
        return self._pool.terminate()

    def __getattr__(self, name):
        return getattr(self._pool, name)
