"""
Pydantic models for the cached database schema profile.

The SchemaProfile is produced once during onboarding and stored as a JSON file.
It is loaded into memory at startup and injected into the agent's system prompt
so the LLM knows the exact schema without querying information_schema on every turn.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    """Metadata for a single database column."""

    name: str
    data_type: str
    is_nullable: bool
    column_default: str | None = None
    comment: str | None = None
    # Populated for text/varchar columns whose distinct count ≤ CARDINALITY_THRESHOLD
    distinct_values: list[str] | None = None
    # Populated for date/timestamp columns
    date_min: str | None = None
    date_max: str | None = None


class ForeignKey(BaseModel):
    """A foreign-key relationship between two columns."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str


class TableInfo(BaseModel):
    """Metadata for a single table or view."""

    name: str
    table_type: str  # 'BASE TABLE' or 'VIEW'
    comment: str | None = None
    row_count: int | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_keys: list[str] = Field(default_factory=list)


class GlossaryEntry(BaseModel):
    """A business-glossary term harvested from a documentation table or column comment."""

    source: str  # e.g. 'table:glossary', 'column_comment:sales.status'
    term: str
    definition: str


class SchemaProfile(BaseModel):
    """
    Complete semantic profile of a Postgres schema.

    Produced once by the onboarding CLI / API and cached as a JSON file keyed
    by a hash of (host, database, schema).  Every chat session loads this once
    and injects it into the agent's system prompt.
    """

    dsn_hash: str
    schema_name: str
    profiled_at: datetime
    tables: list[TableInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    # True when table count > LARGE_SCHEMA_THRESHOLD; future hook for vector retrieval
    is_large_schema: bool = False
    # Absolute path to the JSON cache file (set after saving)
    profile_path: str = ""

    # ── Rendering ────────────────────────────────────────────────────────────

    def render_for_prompt(self) -> str:
        """
        Render the schema profile as structured plain text for injection into
        the agent system prompt.

        Format is designed to be unambiguous for LLMs: table headers, column
        annotations (PK/FK, nullable, distinct values, date ranges), and a
        join graph the agent can use directly when writing SQL.
        """
        lines: list[str] = []

        lines.append(f"=== DATABASE SCHEMA: {self.schema_name} ===")
        lines.append(
            f"Profiled: {self.profiled_at.strftime('%Y-%m-%d %H:%M UTC')}  "
            f"| Tables: {len(self.tables)}  "
            f"| FK relationships: {len(self.foreign_keys)}"
        )
        lines.append("")

        # Build FK lookup: (from_table, from_col) → "to_table.to_col"
        fk_map: dict[tuple[str, str], str] = {
            (fk.from_table, fk.from_column): f"{fk.to_table}.{fk.to_column}"
            for fk in self.foreign_keys
        }

        # Build PK set per table
        pk_set: dict[str, set[str]] = {
            t.name: set(t.primary_keys) for t in self.tables
        }

        for table in self.tables:
            rc = f"{table.row_count:,}" if table.row_count is not None else "unknown"
            lines.append(
                f"TABLE: {table.name}  [{table.table_type}]  ({rc} rows)"
            )
            if table.comment:
                lines.append(f"  Description: {table.comment}")

            lines.append("  Columns:")
            for col in table.columns:
                flags: list[str] = []
                if col.name in pk_set.get(table.name, set()):
                    flags.append("PK")
                if not col.is_nullable:
                    flags.append("NOT NULL")
                fk_target = fk_map.get((table.name, col.name))
                if fk_target:
                    flags.append(f"FK→{fk_target}")

                flag_str = f" [{', '.join(flags)}]" if flags else ""
                parts = [f"    - {col.name} ({col.data_type}){flag_str}"]

                if col.comment:
                    parts.append(f"— {col.comment}")

                if col.distinct_values is not None:
                    shown = col.distinct_values[:25]
                    vals_str = ", ".join(f'"{v}"' for v in shown)
                    if len(col.distinct_values) > 25:
                        vals_str += f" … ({len(col.distinct_values)} total)"
                    parts.append(f"[values: {vals_str}]")

                if col.date_min and col.date_max:
                    parts.append(f"[range: {col.date_min} → {col.date_max}]")

                lines.append("  ".join(parts))

            lines.append("")

        # Join graph
        if self.foreign_keys:
            lines.append("=== JOIN GRAPH (use these exact column names for JOINs) ===")
            for fk in self.foreign_keys:
                lines.append(
                    f"  {fk.from_table}.{fk.from_column} → {fk.to_table}.{fk.to_column}"
                )
            lines.append("")

        # Business glossary
        if self.glossary:
            lines.append(f"=== BUSINESS GLOSSARY ({len(self.glossary)} entries) ===")
            for entry in self.glossary:
                lines.append(f"  [{entry.source}] {entry.term}: {entry.definition}")
            lines.append("")

        return "\n".join(lines)

    # ── Convenience ──────────────────────────────────────────────────────────

    def get_table(self, name: str) -> TableInfo | None:
        """Return TableInfo by name (case-insensitive)."""
        name_lower = name.lower()
        return next(
            (t for t in self.tables if t.name.lower() == name_lower), None
        )

    def summary_dict(self) -> dict[str, Any]:
        """Return a lightweight summary suitable for API responses."""
        return {
            "dsn_hash": self.dsn_hash,
            "schema_name": self.schema_name,
            "profiled_at": self.profiled_at.isoformat(),
            "table_count": len(self.tables),
            "fk_count": len(self.foreign_keys),
            "glossary_entry_count": len(self.glossary),
            "is_large_schema": self.is_large_schema,
            "profile_path": self.profile_path,
        }
