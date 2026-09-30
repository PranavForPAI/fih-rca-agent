"""
Pydantic AI agent: the single Agent instance that drives the RCA conversation.

Architecture decisions:
- One Agent per schema profile (cached in _agent_registry keyed by dsn_hash).
- The full schema profile is injected into the system prompt — no per-turn
  information_schema queries, no vector retrieval for schemas < 100 tables.
- Two tools: run_sql (the workhorse) and list_distinct_values (cardinality
  fallback).  Both are schema-agnostic.
- Queries executed per turn are accumulated in AgentDeps.queries_executed and
  returned in the API response alongside the answer.
- UsageLimits caps LLM round-trips to MAX_TOOL_CALLS to prevent runaway loops.

Model config is read entirely from environment variables so switching providers
is a one-line config change.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.usage import UsageLimits

from app.db import redact_dsn
from app.schema_profile import SchemaProfile
from app.sql_guard import SQLValidationError, validate_and_limit_sql

logger = logging.getLogger(__name__)

# ─── Environment config ───────────────────────────────────────────────────────

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "").lower().strip()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL_NAME: str = os.getenv(
    "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"
)

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

MAX_ROWS: int = int(os.getenv("MAX_ROWS", "500"))
# request_limit: number of LLM API round-trips per agent.run() call
MAX_TOOL_CALLS: int = int(os.getenv("MAX_TOOL_CALLS", "20"))


def resolve_model() -> Model:
    """
    Resolve the configured LLM Model (Gemini or Anthropic) based on env vars.
    Auto-detects provider if LLM_PROVIDER is not explicitly specified.
    """
    provider = os.getenv("LLM_PROVIDER", "").lower().strip()
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    # Treat template placeholder as empty
    if anthropic_key.startswith("sk-ant-..."):
        anthropic_key = ""
    if gemini_key.startswith("AIzaSy..."):
        gemini_key = ""

    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    # Explicit or auto-detected Gemini
    if provider == "gemini" or (not provider and gemini_key and not anthropic_key):
        if not gemini_key:
            raise RuntimeError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set in environment or .env file."
            )
        logger.info("Using Gemini model: %s", gemini_model)
        return GoogleModel(
            gemini_model,
            provider=GoogleProvider(api_key=gemini_key),
        )

    # Explicit or auto-detected Anthropic
    if provider == "anthropic" or (not provider and anthropic_key):
        if not anthropic_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set in environment or .env file."
            )
        logger.info("Using Anthropic model: %s", anthropic_model)
        return AnthropicModel(
            anthropic_model,
            provider=AnthropicProvider(api_key=anthropic_key),
        )

    # Fallback if both keys are present and no provider specified
    if gemini_key:
        logger.info("Defaulting to Gemini model: %s", gemini_model)
        return GoogleModel(
            gemini_model,
            provider=GoogleProvider(api_key=gemini_key),
        )

    raise RuntimeError(
        "No LLM API key configured. Set GEMINI_API_KEY (or GOOGLE_API_KEY) "
        "or ANTHROPIC_API_KEY in your .env file."
    )


# ─── Agent dependencies (injected via RunContext) ─────────────────────────────


@dataclass
class AgentDeps:
    """
    All context the agent's tools need, injected per agent.run() call.

    queries_executed accumulates every SQL statement executed this turn so
    main.py can return them in the API response for UI provenance display.
    """

    db_pool: asyncpg.Pool
    schema_profile: SchemaProfile
    max_rows: int = MAX_ROWS
    session_id: str = ""
    queries_executed: list[dict[str, Any]] = field(default_factory=list)


# ─── System prompt ────────────────────────────────────────────────────────────

_RCA_METHOD = """
## Your Role

You are an expert data analyst and root-cause-analysis (RCA) assistant with
direct, read-only SQL access to the Postgres database described below.  Your
job is to help users understand what their data shows — especially when
something looks off, unexpected, or needs explaining to stakeholders.

## RCA Procedure

When a question implies an investigation, follow this procedure.  Not every
step is needed for simple factual questions — a question like "how many orders
today?" only needs step 1.

1. **Quantify.**  Convert vague questions ("how did we do?") into a concrete
   metric with an explicit time window and grouping.  Use the schema below to
   find the correct tables and column names — never guess them.

2. **Localize.**  Before speculating about causes, determine *where* a gap
   concentrates — which store, category, account, product, or segment accounts
   for the majority of the discrepancy.

3. **Decompose.**  Where the schema supports it, break a metric into its parts
   (e.g. transaction count × average size, or price × volume) so you can say
   precisely *what* changed, not just *that* something changed.

4. **Correlate multiple signals.**  When more than one table or metric shows an
   anomaly for the same entity or time window, name all of them together as
   concurrent signals worth investigating.

5. **Separate observation from causation — CRITICAL RULE.**
   Always state plainly what the data shows (a correlation, a timing
   coincidence) and what it does NOT establish (that A caused B).  Never assert
   causation that a query hasn't actually demonstrated.  When asked "what's
   driving it?", present the strongest correlated signals and explicitly state
   that establishing true causation requires further investigation.

6. **Carry context across turns.**  The conversation history contains prior
   turns.  Inherit time windows, metrics, entity filters, and context from
   those turns — never make the user repeat themselves.  "Where did we lose the
   most?" inherits the metric and period from the previous turn.

7. **When summarising or briefing.**  Close with concrete, specific next steps
   traceable to actual numbers found in this conversation — not generic
   best-practice advice.

8. **Never fabricate schema artifacts.**  Every table name, column name, and
   filter value must come from the schema profile below or a tool call — never
   invent them.  If unsure about valid filter values, call
   `list_distinct_values` before filtering.

9. **Be conversational and concise** by default.  Provide extra methodological
   detail only when the user explicitly asks (e.g. "walk me through how you got
   that").

## Query Guidelines

- Prefer `GROUP BY` with aggregations over pulling raw rows.
- Always include an explicit time window in time-series queries.
- Call `list_distinct_values` when you need valid values for a filter that
  wasn't captured in the schema profile.
- The system automatically adds `LIMIT {max_rows}` to queries without one —
  write aggregations, not full table scans.
- If a query fails, read the error message; it usually indicates a schema
  mismatch.  Cross-check column names against the profile and retry.

## Database Schema

{schema_context}
""".strip()


def build_system_prompt(profile: SchemaProfile, max_rows: int = MAX_ROWS) -> str:
    """Render the full system prompt with the schema profile embedded."""
    return _RCA_METHOD.format(
        max_rows=max_rows,
        schema_context=profile.render_for_prompt(),
    )


# ─── Agent factory ────────────────────────────────────────────────────────────


def create_agent(profile: SchemaProfile) -> "Agent[AgentDeps, str]":
    """
    Construct a Pydantic AI Agent for the given schema profile.

    The agent's system prompt is fixed at creation time (includes the full
    schema profile text).  Tools are registered on the returned agent object.
    """
    model = resolve_model()

    system_prompt = build_system_prompt(profile)
    max_rows = profile.is_large_schema and MAX_ROWS or MAX_ROWS  # same for now

    rca_agent: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        output_type=str,
        system_prompt=system_prompt,
    )

    # ── Tool: run_sql ─────────────────────────────────────────────────────────

    @rca_agent.tool
    async def run_sql(ctx: RunContext[AgentDeps], query: str) -> str:
        """
        Execute a read-only SQL SELECT query against the database.

        Use this to answer quantitative questions, aggregate data, find
        anomalies, and correlate signals across tables.  CTEs (WITH … SELECT)
        are fully supported.  The result is returned as JSON containing the
        executed SQL, column names, row data, and row count.

        Prefer GROUP BY aggregations over raw row fetches — the system caps
        results at the configured row limit automatically.

        Do NOT include a trailing semicolon.  The system will validate and
        safely limit the query before execution.
        """
        deps = ctx.deps
        logger.info(
            "[session=%s] run_sql called: %.300s…", deps.session_id, query
        )

        # Validate with sqlglot — rejects non-SELECT, injects LIMIT
        try:
            validated_sql = validate_and_limit_sql(
                query, max_rows=deps.max_rows
            )
        except SQLValidationError as exc:
            logger.warning(
                "[session=%s] SQL rejected: %s", deps.session_id, exc
            )
            return json.dumps(
                {
                    "error": str(exc),
                    "hint": (
                        "Only SELECT statements are allowed.  "
                        "Rewrite as a SELECT (or WITH … SELECT) query."
                    ),
                }
            )

        logger.info(
            "[session=%s] Executing: %s", deps.session_id, validated_sql
        )

        # Execute
        try:
            async with deps.db_pool.acquire() as conn:
                rows = await conn.fetch(validated_sql)
        except asyncpg.exceptions.QueryCanceledError:
            logger.warning(
                "[session=%s] Query timed out", deps.session_id
            )
            return json.dumps(
                {
                    "error": "Query timed out (statement_timeout exceeded).",
                    "sql": validated_sql,
                    "hint": "Simplify the query or add more selective filters.",
                }
            )
        except Exception as exc:
            logger.error(
                "[session=%s] Query error: %s", deps.session_id, exc
            )
            return json.dumps(
                {
                    "error": f"Query failed: {exc}",
                    "sql": validated_sql,
                    "hint": (
                        "Check table and column names against the schema "
                        "profile; they are case-sensitive in Postgres."
                    ),
                }
            )

        # Serialize result
        if not rows:
            result: dict[str, Any] = {
                "sql": validated_sql,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
            }
        else:
            columns = list(rows[0].keys())
            row_data = [dict(r) for r in rows]
            result = {
                "sql": validated_sql,
                "columns": columns,
                "rows": row_data,
                "row_count": len(row_data),
                "truncated": len(row_data) >= deps.max_rows,
            }

        # Accumulate for API response provenance
        deps.queries_executed.append(
            {
                "sql": validated_sql,
                "row_count": result["row_count"],
            }
        )

        logger.info(
            "[session=%s] Query OK — %d rows (truncated=%s)",
            deps.session_id,
            result["row_count"],
            result["truncated"],
        )

        return json.dumps(result, default=str)

    # ── Tool: list_distinct_values ────────────────────────────────────────────

    @rca_agent.tool
    async def list_distinct_values(
        ctx: RunContext[AgentDeps], table: str, column: str
    ) -> str:
        """
        Return the distinct non-null values for a column in a table.

        Use this when you need valid filter values that weren't captured in the
        schema profile (e.g. high-cardinality columns that exceeded the
        profiling threshold, or columns added since last onboarding).

        Returns JSON with keys: table, column, values (list[str]), count.
        """
        deps = ctx.deps
        schema = deps.schema_profile.schema_name
        logger.info(
            "[session=%s] list_distinct_values: %s.%s",
            deps.session_id,
            table,
            column,
        )

        # Safe SQL — identifiers are double-quoted
        safe_col = column.replace('"', '""')
        safe_tbl = table.replace('"', '""')
        safe_sch = schema.replace('"', '""')
        sql = (
            f'SELECT DISTINCT "{safe_col}"::text AS v '
            f'FROM "{safe_sch}"."{safe_tbl}" '
            f'WHERE "{safe_col}" IS NOT NULL '
            f'ORDER BY 1 LIMIT 200'
        )

        try:
            async with deps.db_pool.acquire() as conn:
                rows = await conn.fetch(sql)
        except Exception as exc:
            logger.error(
                "[session=%s] list_distinct_values error: %s",
                deps.session_id,
                exc,
            )
            return json.dumps(
                {"error": str(exc), "table": table, "column": column}
            )

        values = [r["v"] for r in rows if r["v"] is not None]
        return json.dumps(
            {
                "table": table,
                "column": column,
                "values": values,
                "count": len(values),
            }
        )

    return rca_agent


# ─── Agent registry ───────────────────────────────────────────────────────────
# One agent per dsn_hash keeps tool registrations alive without recreating
# the Agent on every request.  Creating a new Agent is cheap, but the registry
# also avoids tool-registration duplication inside a long-lived process.

_registry: dict[str, "Agent[AgentDeps, str]"] = {}


def get_or_create_agent(profile: SchemaProfile) -> "Agent[AgentDeps, str]":
    """Return (and cache) an Agent for the given schema profile."""
    key = profile.dsn_hash
    if key not in _registry:
        logger.info("Building agent for dsn_hash=%s", key)
        _registry[key] = create_agent(profile)
    return _registry[key]


def invalidate_agent(dsn_hash: str) -> None:
    """Remove a cached agent so the next request rebuilds it with the new profile."""
    removed = _registry.pop(dsn_hash, None)
    if removed:
        logger.info("Agent invalidated for dsn_hash=%s", dsn_hash)


def get_usage_limits() -> UsageLimits:
    """Return UsageLimits configured from environment variables."""
    return UsageLimits(request_limit=MAX_TOOL_CALLS)
