"""Owner-level database access for test fixtures only.

Services run as the restricted runtime role (GCOR_DB_USER); fixtures that create relay
tables, seed identity rows or alter constraints use POSTGRES_ADMIN_USER/PASSWORD, which
default to the runtime credentials for deployments that have not separated roles yet.
"""
import os
import asyncpg


async def admin_pool():
    import main
    user = os.environ.get('POSTGRES_ADMIN_USER') or main.POSTGRES_USER
    password = os.environ.get('POSTGRES_ADMIN_PASSWORD') or main.POSTGRES_PASSWORD
    return await asyncpg.create_pool(host=main.POSTGRES_HOST, port=main.POSTGRES_PORT, user=user, password=password,
                                     database=main.POSTGRES_DB, min_size=1, max_size=2)
