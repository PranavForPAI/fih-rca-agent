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

_FORBIDDEN_FUNCTIONS = {
    "set_config",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "pg_sleep",
    "pg_cancel_backend",
    "pg_terminate_backend",
    "pg_reload_conf",
    "nextval",
    "setval",
    "current_database",
    "current_user",
    "current_role",
    "session_user",
    "current_schema",
    "current_setting",
    "query_to_xml",
    "query_to_xmlschema",
    "table_to_xml",
    "table_to_xmlschema",
    "dblink",
    "dblink_exec",
    "lo_import",
    "lo_export",
}


class SQLValidationError(Exception):
    """Raised when a query fails security or structural validation."""


def validate_and_limit_sql(
    query: str,
    max_rows: int = 500,
    *,
    allowed_schema: str | None = None,
    allowed_tables: set[str] | None = None,
) -> str:
    """
    Parse, validate, and (if needed) add a LIMIT to a SQL query.

    Args:
        query:    Raw SQL string from the agent.
        max_rows: Maximum rows to return; LIMIT is injected if absent.
        allowed_schema: Optional schema to constrain relation references to.
        allowed_tables: Optional set of profiled tables/views to allow.

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

    if (allowed_schema is None) != (allowed_tables is None):
        raise ValueError("allowed_schema and allowed_tables must be provided together.")

    for function in stmt.find_all(exp.Func):
        function_name = (
            function.name
            if isinstance(function, exp.Anonymous)
            else function.sql_name()
        ).casefold()
        if function_name.casefold() in _FORBIDDEN_FUNCTIONS:
            raise SQLValidationError(
                f"Function {function_name} is not allowed in agent queries."
            )

    if allowed_schema is not None and allowed_tables is not None:
        cte_names = {cte.alias_or_name for cte in stmt.find_all(exp.CTE)}
        profiled_relations = 0
        for table in stmt.find_all(exp.Table):
            if (
                table.name in cte_names
                and not table.db
                and not table.catalog
            ):
                continue
            if table.catalog or (table.db and table.db != allowed_schema):
                raise SQLValidationError(
                    f"Queries may only read tables in schema {allowed_schema}."
                )
            if table.name not in allowed_tables:
                raise SQLValidationError(
                    f"Table {table.name} is not part of the onboarded schema."
                )
            profiled_relations += 1
            if not table.db:
                table.set("db", exp.to_identifier(allowed_schema, quoted=True))
        if profiled_relations == 0:
            raise SQLValidationError(
                "Agent queries must read at least one profiled table or view."
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
