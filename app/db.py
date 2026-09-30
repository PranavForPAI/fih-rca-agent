"""
asyncpg connection-pool helpers for both the introspection (onboarding) role
and the read-only agent role.

The two roles are deliberately kept separate:
- Introspection pool: uses whatever credentials the admin provides (needs
  SELECT on information_schema / pg_catalog).
- Agent pool: uses a dedicated read-only role (rca_agent) that has only SELECT
  rights and statement_timeout + default_transaction_read_only enforced at the
  role level — see README for setup SQL.

Connection strings are never logged; use redact_dsn() before any log statement
that mentions a DSN.
"""
from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlparse

import asyncpg

logger = logging.getLogger(__name__)


# ─── DSN utilities ───────────────────────────────────────────────────────────


def redact_dsn(dsn: str) -> str:
    """Replace the password portion of a DSN with *** for safe logging."""
    try:
        parsed = urlparse(dsn)
        if parsed.password:
            return dsn.replace(f":{parsed.password}@", ":***@")
        return dsn
    except Exception:
        return "<dsn-redacted>"


def get_dsn_hash(dsn: str, schema: str = "public") -> str:
    """
    Produce a stable 16-char hex hash from (host, port, database, schema).

    Used as the cache-file key for the schema profile.  Never includes
    credentials — safe to use in filenames and log output.
    """
    try:
        parsed = urlparse(dsn)
        key = (
            f"{parsed.hostname or 'localhost'}:"
            f"{parsed.port or 5432}/"
            f"{(parsed.path or '/').lstrip('/')}/"
            f"{schema}"
        )
    except Exception:
        key = dsn + "/" + schema
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ─── Pool factories ───────────────────────────────────────────────────────────


async def create_introspection_pool(
    dsn: str,
    min_size: int = 1,
    max_size: int = 3,
    command_timeout: float = 30.0,
) -> asyncpg.Pool:
    """
    Create an asyncpg pool for schema introspection (onboarding).

    Uses the credentials in the DSN as provided — typically a database owner
    or superuser account that can read information_schema / pg_catalog and
    all user tables.
    """
    logger.info("Creating introspection pool → %s", redact_dsn(dsn))
    pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
    )
    return pool  # type: ignore[return-value]


async def create_agent_pool(
    dsn: str,
    min_size: int = 1,
    max_size: int = 10,
    command_timeout: float = 15.0,
) -> asyncpg.Pool:
    """
    Create an asyncpg pool for the read-only RCA agent.

    The DSN should point to a role that has been hardened at the Postgres level:

        CREATE ROLE rca_agent LOGIN PASSWORD '...' NOSUPERUSER;
        GRANT USAGE ON SCHEMA <schema> TO rca_agent;
        GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO rca_agent;
        ALTER DEFAULT PRIVILEGES IN SCHEMA <schema>
            GRANT SELECT ON TABLES TO rca_agent;
        ALTER ROLE rca_agent SET statement_timeout = '10s';
        ALTER ROLE rca_agent SET default_transaction_read_only = on;

    command_timeout is set slightly above statement_timeout (10s) so asyncpg
    sees a clean error from Postgres rather than a socket timeout.
    """
    logger.info("Creating agent (read-only) pool → %s", redact_dsn(dsn))
    pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
    )
    return pool  # type: ignore[return-value]
