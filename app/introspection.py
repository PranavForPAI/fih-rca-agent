"""
Schema introspection: crawls information_schema and pg_catalog to build a
complete SchemaProfile for any Postgres database.

Called once during onboarding; results are cached as JSON.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import asyncpg

from app.schema_profile import (
    ColumnInfo,
    ForeignKey,
    GlossaryEntry,
    SchemaProfile,
    TableInfo,
)

logger = logging.getLogger(__name__)

# ─── Thresholds (tunable via constants) ──────────────────────────────────────

# Distinct values ≤ this → fetch and store actual values
CARDINALITY_THRESHOLD: int = 50
# Tables > this → flag is_large_schema (future: use vector retrieval)
LARGE_SCHEMA_THRESHOLD: int = 100
# Regex to identify documentation / glossary tables
GLOSSARY_TABLE_RE = re.compile(
    r"readme|glossary|dictionar|notation|definition|metadata|dataset_note",
    re.IGNORECASE,
)
# Column name candidates for glossary "term" and "definition"
_TERM_COLS = {"term", "topic", "name", "key", "title", "field", "concept", "item"}
_DEF_COLS = {
    "definition", "detail", "description", "value", "explanation",
    "meaning", "notes", "note", "desc",
}


# ─── Main entry point ─────────────────────────────────────────────────────────


async def run_introspection(
    pool: asyncpg.Pool,
    schema: str = "public",
    glossary_table: str | None = None,
    dsn_hash: str = "",
) -> SchemaProfile:
    """
    Perform a full introspection pass and return a SchemaProfile.

    Steps:
    1. Fetch table/view list + comments
    2. Fetch columns + PK/FK relationships
    3. Fetch approximate row counts
    4. Profile low-cardinality text columns (distinct values)
    5. Profile date/timestamp columns (MIN/MAX range)
    6. Harvest business glossary
    """
    logger.info("Introspection start — schema=%r", schema)

    async with pool.acquire() as conn:
        # ── 1. Tables & views ─────────────────────────────────────────────────
        raw_tables = await _fetch_tables(conn, schema)
        logger.info("Found %d tables/views", len(raw_tables))

        # ── 2. Columns, PKs ───────────────────────────────────────────────────
        all_columns: dict[str, list[ColumnInfo]] = {}
        for name, ttype, comment in raw_tables:
            all_columns[name] = await _fetch_columns(conn, schema, name)

        pk_rows = await _fetch_primary_keys(conn, schema)
        pks_by_table: dict[str, list[str]] = {}
        for tname, colname in pk_rows:
            pks_by_table.setdefault(tname, []).append(colname)

        # ── 3. Foreign keys ────────────────────────────────────────────────────
        fk_rows = await _fetch_foreign_keys(conn, schema)

        # ── 4. Row counts ──────────────────────────────────────────────────────
        row_counts = await _fetch_row_counts(conn, schema)

        # ── 5. Profile columns (cardinality + date ranges) ─────────────────────
        # Run table profiles concurrently (one task per table)
        profile_tasks = []
        for name, ttype, _ in raw_tables:
            if ttype == "VIEW":
                continue  # skip heavy profiling for views
            cols = all_columns.get(name, [])
            est = row_counts.get(name)
            profile_tasks.append(_profile_columns(conn, schema, name, cols, est))

        if profile_tasks:
            await asyncio.gather(*profile_tasks, return_exceptions=True)

        # ── 6. Glossary ────────────────────────────────────────────────────────
        glossary = await _harvest_glossary(conn, schema, glossary_table)

    # ── Assemble TableInfo objects ─────────────────────────────────────────────
    tables = [
        TableInfo(
            name=name,
            table_type=ttype,
            comment=comment,
            row_count=row_counts.get(name),
            columns=all_columns.get(name, []),
            primary_keys=pks_by_table.get(name, []),
        )
        for name, ttype, comment in raw_tables
    ]

    fks = [
        ForeignKey(
            from_table=r["from_table"],
            from_column=r["from_column"],
            to_table=r["to_table"],
            to_column=r["to_column"],
        )
        for r in fk_rows
    ]

    profile = SchemaProfile(
        dsn_hash=dsn_hash,
        schema_name=schema,
        profiled_at=datetime.now(timezone.utc),
        tables=tables,
        foreign_keys=fks,
        glossary=glossary,
        is_large_schema=len(tables) > LARGE_SCHEMA_THRESHOLD,
    )

    logger.info(
        "Introspection complete — %d tables, %d FKs, %d glossary entries",
        len(tables),
        len(fks),
        len(glossary),
    )
    return profile


# ─── Private helpers ──────────────────────────────────────────────────────────


async def _fetch_tables(
    conn: asyncpg.Connection, schema: str
) -> list[tuple[str, str, str | None]]:
    """Return [(table_name, table_type, comment)] for all tables and views."""
    rows = await conn.fetch(
        """
        SELECT
            t.table_name,
            t.table_type,
            obj_description(
                (quote_ident(t.table_schema) || '.' || quote_ident(t.table_name))::regclass,
                'pg_class'
            ) AS comment
        FROM information_schema.tables t
        WHERE t.table_schema = $1
          AND t.table_type IN ('BASE TABLE', 'VIEW')
        ORDER BY t.table_type DESC, t.table_name
        """,
        schema,
    )
    return [(r["table_name"], r["table_type"], r["comment"]) for r in rows]


async def _fetch_columns(
    conn: asyncpg.Connection, schema: str, table: str
) -> list[ColumnInfo]:
    """Return ColumnInfo list for a table, ordered by ordinal position."""
    rows = await conn.fetch(
        """
        SELECT
            c.column_name,
            c.data_type,
            (c.is_nullable = 'YES') AS is_nullable,
            c.column_default,
            col_description(
                (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                c.ordinal_position
            ) AS comment
        FROM information_schema.columns c
        WHERE c.table_schema = $1
          AND c.table_name  = $2
        ORDER BY c.ordinal_position
        """,
        schema,
        table,
    )
    return [
        ColumnInfo(
            name=r["column_name"],
            data_type=r["data_type"],
            is_nullable=bool(r["is_nullable"]),
            column_default=r["column_default"],
            comment=r["comment"],
        )
        for r in rows
    ]


async def _fetch_primary_keys(
    conn: asyncpg.Connection, schema: str
) -> list[tuple[str, str]]:
    """Return [(table_name, column_name)] for all PKs in the schema."""
    rows = await conn.fetch(
        """
        SELECT
            tc.table_name,
            kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON  tc.constraint_name = kcu.constraint_name
            AND tc.table_schema    = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema    = $1
        ORDER BY tc.table_name, kcu.ordinal_position
        """,
        schema,
    )
    return [(r["table_name"], r["column_name"]) for r in rows]


async def _fetch_foreign_keys(
    conn: asyncpg.Connection, schema: str
) -> list[dict]:
    """Return FK relationship dicts: from_table, from_column, to_table, to_column."""
    rows = await conn.fetch(
        """
        SELECT
            kcu.table_name  AS from_table,
            kcu.column_name AS from_column,
            ccu.table_name  AS to_table,
            ccu.column_name AS to_column
        FROM information_schema.referential_constraints rc
        JOIN information_schema.key_column_usage kcu
            ON  rc.constraint_name   = kcu.constraint_name
            AND kcu.constraint_schema = $1
        JOIN information_schema.constraint_column_usage ccu
            ON  rc.unique_constraint_name = ccu.constraint_name
            AND ccu.constraint_schema     = $1
        ORDER BY kcu.table_name, kcu.column_name
        """,
        schema,
    )
    return [dict(r) for r in rows]


async def _fetch_row_counts(
    conn: asyncpg.Connection, schema: str
) -> dict[str, int]:
    """
    Return approximate row counts from pg_stat_user_tables.

    These are live tuple estimates updated by autovacuum; they can be
    slightly stale for very active tables but are cheap to read.
    """
    rows = await conn.fetch(
        """
        SELECT relname AS table_name, n_live_tup AS row_count
        FROM   pg_stat_user_tables
        WHERE  schemaname = $1
        """,
        schema,
    )
    return {r["table_name"]: int(r["row_count"]) for r in rows}


async def _profile_columns(
    conn: asyncpg.Connection,
    schema: str,
    table: str,
    columns: list[ColumnInfo],
    est_row_count: int | None,
) -> None:
    """
    In-place: populate distinct_values for low-cardinality text columns,
    and date_min/date_max for date/timestamp columns.
    """
    _TEXT = {"character varying", "varchar", "text", "char", "character", "name"}
    _DATE = {
        "date",
        "timestamp without time zone",
        "timestamp with time zone",
        "timestamp",
        "timestamptz",
    }

    for col in columns:
        dtype = col.data_type.lower()
        qschema = _q(schema)
        qtable = _q(table)
        qcol = _q(col.name)

        if any(dtype.startswith(t) for t in _TEXT):
            # Check cardinality first
            try:
                card_row = await conn.fetchrow(
                    f"SELECT COUNT(DISTINCT {qcol}) AS cnt FROM {qschema}.{qtable}"
                )
                cardinality = int(card_row["cnt"]) if card_row else 0
                if 0 < cardinality <= CARDINALITY_THRESHOLD:
                    val_rows = await conn.fetch(
                        f"SELECT DISTINCT {qcol}::text AS v "
                        f"FROM {qschema}.{qtable} "
                        f"WHERE {qcol} IS NOT NULL "
                        f"ORDER BY 1 LIMIT 100"
                    )
                    col.distinct_values = [r["v"] for r in val_rows]
            except Exception as exc:
                logger.debug(
                    "Cardinality check failed for %s.%s: %s", table, col.name, exc
                )

        elif any(dtype.startswith(t) for t in _DATE):
            try:
                rng = await conn.fetchrow(
                    f"SELECT MIN({qcol})::text AS mn, MAX({qcol})::text AS mx "
                    f"FROM {qschema}.{qtable} WHERE {qcol} IS NOT NULL"
                )
                if rng and rng["mn"]:
                    col.date_min = rng["mn"]
                    col.date_max = rng["mx"]
            except Exception as exc:
                logger.debug(
                    "Date range failed for %s.%s: %s", table, col.name, exc
                )


def _q(identifier: str) -> str:
    """Double-quote a Postgres identifier."""
    # Escape any embedded double-quotes
    safe = identifier.replace('"', '""')
    return f'"{safe}"'


# ─── Glossary harvesting ──────────────────────────────────────────────────────


async def _harvest_glossary(
    conn: asyncpg.Connection,
    schema: str,
    explicit_table: str | None,
) -> list[GlossaryEntry]:
    """
    Collect business glossary entries from:
    - An explicitly named table (--glossary-table flag)
    - Tables whose names match GLOSSARY_TABLE_RE (auto-detected)
    """
    candidates: set[str] = set()

    if explicit_table:
        candidates.add(explicit_table)

    # Auto-detect by table name pattern
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = $1",
        schema,
    )
    for r in rows:
        if GLOSSARY_TABLE_RE.search(r["table_name"]):
            candidates.add(r["table_name"])

    entries: list[GlossaryEntry] = []
    for tname in sorted(candidates):
        try:
            entries.extend(await _read_glossary_table(conn, schema, tname))
        except Exception as exc:
            logger.warning("Failed to read glossary from %s: %s", tname, exc)

    return entries


async def _read_glossary_table(
    conn: asyncpg.Connection, schema: str, table: str
) -> list[GlossaryEntry]:
    """
    Read a glossary table by detecting (term, definition) column pairs.

    Detection strategy:
    1. Look for columns whose names are in _TERM_COLS / _DEF_COLS.
    2. Fall back to first two text columns.
    """
    col_rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        ORDER BY ordinal_position
        """,
        schema,
        table,
    )
    cols = [r["column_name"].lower() for r in col_rows]
    original_cols = [r["column_name"] for r in col_rows]

    # Find term column
    term_col = next((original_cols[i] for i, c in enumerate(cols) if c in _TERM_COLS), None)
    def_col = next((original_cols[i] for i, c in enumerate(cols) if c in _DEF_COLS), None)

    # Fall back to first two columns
    if not term_col and len(original_cols) >= 1:
        term_col = original_cols[0]
    if not def_col and len(original_cols) >= 2:
        def_col = original_cols[1]

    if not term_col or not def_col or term_col == def_col:
        logger.debug("No usable (term, definition) pair in table %s", table)
        return []

    rows = await conn.fetch(
        f'SELECT {_q(term_col)}::text AS term, {_q(def_col)}::text AS definition '
        f'FROM {_q(schema)}.{_q(table)} '
        f'WHERE {_q(term_col)} IS NOT NULL AND {_q(def_col)} IS NOT NULL '
        f'LIMIT 500'
    )

    return [
        GlossaryEntry(
            source=f"table:{table}",
            term=r["term"].strip(),
            definition=r["definition"].strip(),
        )
        for r in rows
        if r["term"] and r["definition"]
    ]
