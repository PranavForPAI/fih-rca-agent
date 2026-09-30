"""
sqlglot-based SQL validation and LIMIT injection.

Every query the agent tries to execute passes through validate_and_limit_sql()
before it touches the database.  This enforces:

1. Exactly one statement per call (no multi-statement batches).
2. Only SELECT (or WITH ... SELECT) is allowed — no DDL, DML, COPY, etc.
3. LIMIT is auto-injected if not present, capped at max_rows.

Raises SQLValidationError with a user-safe message on any violation.
The message is returned to the agent so it can rewrite the query.
"""
from __future__ import annotations

import logging

import sqlglot
import sqlglot.errors
from sqlglot import exp

logger = logging.getLogger(__name__)

# Statement types that are unconditionally rejected
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Transaction,
    exp.Merge,
    exp.TruncateTable,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
)


class SQLValidationError(Exception):
    """Raised when a query fails security or structural validation."""


def validate_and_limit_sql(query: str, max_rows: int = 500) -> str:
    """
    Parse, validate, and (if needed) add a LIMIT to a SQL query.

    Args:
        query:    Raw SQL string from the agent.
        max_rows: Maximum rows to return; LIMIT is injected if absent.

    Returns:
        A validated SQL string (dialect=postgres) safe to execute.

    Raises:
        SQLValidationError: With a descriptive message the agent can act on.
    """
    query = query.strip().rstrip(";").strip()

    if not query:
        raise SQLValidationError("Query is empty.")

    # ── Parse ────────────────────────────────────────────────────────────────
    try:
        statements = sqlglot.parse(
            query,
            read="postgres",
            error_level=sqlglot.ErrorLevel.RAISE,
        )
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError(f"SQL parse error: {exc}") from exc

    # Drop any None items (trailing semicolons can produce them)
    statements = [s for s in statements if s is not None]

    if not statements:
        raise SQLValidationError("No SQL statement found.")

    if len(statements) > 1:
        raise SQLValidationError(
            f"Multiple statements detected ({len(statements)}). "
            "Submit a single SELECT statement per call."
        )

    stmt = statements[0]

    # ── Reject forbidden statement types ─────────────────────────────────────
    for forbidden_cls in _FORBIDDEN:
        if isinstance(stmt, forbidden_cls):
            stmt_name = type(stmt).__name__.upper()
            raise SQLValidationError(
                f"{stmt_name} statements are not allowed. "
                "Only SELECT (or WITH … SELECT) queries are permitted."
            )

    # ── Must be SELECT (covers CTEs: WITH … SELECT parses as exp.Select) ─────
    if not isinstance(stmt, exp.Select):
        stmt_type = type(stmt).__name__.upper()
        raise SQLValidationError(
            f"Expected a SELECT statement, got {stmt_type}. "
            "Rewrite your query as a SELECT."
        )

    # ── Auto-inject LIMIT ────────────────────────────────────────────────────
    existing_limit = stmt.args.get("limit")
    if existing_limit is None:
        stmt = stmt.limit(max_rows)
        logger.debug("Auto-injected LIMIT %d", max_rows)
    else:
        # Respect existing LIMIT but cap it at max_rows
        try:
            lim_expr = getattr(existing_limit, "expression", None) or getattr(existing_limit, "this", None)
            limit_val = int(str(lim_expr))
            if limit_val > max_rows:
                stmt = stmt.limit(max_rows)
                logger.debug(
                    "Capped LIMIT from %d to %d", limit_val, max_rows
                )
        except (AttributeError, ValueError):
            pass  # Non-literal LIMIT — leave as-is

    validated = stmt.sql(dialect="postgres")
    logger.debug("Validated SQL: %s", validated)
    return validated
