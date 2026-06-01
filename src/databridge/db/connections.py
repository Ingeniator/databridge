from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg


@dataclass
class ConnectionRow:
    id: UUID
    owner_key: str
    label: str
    type: str
    role: str
    connection_url: str
    credentials_enc: bytes
    status: str
    last_tested_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


def _row(record: asyncpg.Record) -> ConnectionRow:
    return ConnectionRow(**dict(record))


async def insert_connection(
    pool: asyncpg.Pool,
    *,
    owner_key: str,
    label: str,
    type: str,
    role: str,
    connection_url: str,
    credentials_enc: bytes,
) -> asyncpg.Record:
    return await pool.fetchrow(
        """
        INSERT INTO connections
            (owner_key, label, type, role, connection_url, credentials_enc)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        owner_key, label, type, role, connection_url, credentials_enc,
    )


async def get_connection(pool: asyncpg.Pool, *, id: UUID, owner_key: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT * FROM connections WHERE id = $1 AND owner_key = $2",
        id, owner_key,
    )


async def list_connections(pool: asyncpg.Pool, *, owner_key: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT * FROM connections WHERE owner_key = $1 ORDER BY created_at DESC",
        owner_key,
    )


async def update_connection(
    pool: asyncpg.Pool,
    *,
    id: UUID,
    owner_key: str,
    label: str | None = None,
    credentials_enc: bytes | None = None,
) -> asyncpg.Record | None:
    if label is None and credentials_enc is None:
        return await get_connection(pool, id=id, owner_key=owner_key)

    parts, args = [], [owner_key, id]
    if label is not None:
        parts.append(f"label = ${len(args) + 1}")
        args.append(label)
    if credentials_enc is not None:
        parts.append(f"credentials_enc = ${len(args) + 1}")
        args.append(credentials_enc)
        parts.append("status = 'untested'")
        parts.append("last_tested_at = NULL")
    parts.append("updated_at = now()")
    set_clause = ", ".join(parts)

    return await pool.fetchrow(
        f"UPDATE connections SET {set_clause} WHERE owner_key = $1 AND id = $2 RETURNING *",
        *args,
    )


async def delete_connection(pool: asyncpg.Pool, *, id: UUID, owner_key: str) -> bool:
    result = await pool.execute(
        "DELETE FROM connections WHERE id = $1 AND owner_key = $2",
        id, owner_key,
    )
    return result == "DELETE 1"


async def update_connection_status(
    pool: asyncpg.Pool,
    *,
    id: UUID,
    status: str,
    last_tested_at: datetime,
) -> None:
    await pool.execute(
        "UPDATE connections SET status = $1, last_tested_at = $2 WHERE id = $3",
        status, last_tested_at, id,
    )


async def count_referencing_jobs(pool: asyncpg.Pool, *, connection_id: UUID) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM sync_jobs WHERE connection_id = $1",
        connection_id,
    )
