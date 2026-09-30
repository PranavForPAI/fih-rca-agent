"""
FastAPI application: /onboard and /chat are the complete public surface.

No frontend is served here.  The OpenAPI docs page (at /docs) provides a
live interface for testing and serves as the integration spec for the
separate chat UI project.

Session state (message history) is held in-memory in sessions.py.
Schema profiles are cached to disk in PROFILES_DIR and loaded into
_profile_cache on first access.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from app.agent import (
    AgentDeps,
    get_or_create_agent,
    get_usage_limits,
    invalidate_agent,
    MAX_ROWS,
)
from app.db import (
    create_agent_pool,
    create_introspection_pool,
    get_dsn_hash,
    redact_dsn,
)
from app.introspection import run_introspection
from app.schema_profile import SchemaProfile
from app.sessions import session_store

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Storage config ───────────────────────────────────────────────────────────

PROFILES_DIR = Path(os.getenv("PROFILES_DIR", "schema_profiles"))
PROFILES_DIR.mkdir(exist_ok=True)

# In-process caches (one profile and one pool per unique (host, db, schema))
_profile_cache: dict[str, SchemaProfile] = {}
_pool_cache: dict[str, asyncpg.Pool] = {}


# ─── Profile helpers ──────────────────────────────────────────────────────────


def _profile_path(dsn_hash: str) -> Path:
    return PROFILES_DIR / f"{dsn_hash}.json"


def _load_profile_disk(dsn_hash: str) -> SchemaProfile | None:
    path = _profile_path(dsn_hash)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = SchemaProfile.model_validate(data)
        profile.profile_path = str(path.resolve())
        return profile
    except Exception as exc:
        logger.warning("Corrupt profile at %s: %s", path, exc)
        return None


def _save_profile_disk(profile: SchemaProfile) -> None:
    path = _profile_path(profile.dsn_hash)
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    profile.profile_path = str(path.resolve())
    logger.info("Profile saved → %s", path)


async def _get_profile(dsn: str, schema: str) -> SchemaProfile | None:
    key = get_dsn_hash(dsn, schema)
    if key in _profile_cache:
        return _profile_cache[key]
    profile = _load_profile_disk(key)
    if profile:
        _profile_cache[key] = profile
    return profile


async def _run_onboarding(
    dsn: str,
    schema: str,
    glossary_table: str | None,
    refresh: bool,
) -> SchemaProfile:
    key = get_dsn_hash(dsn, schema)

    if not refresh:
        existing = await _get_profile(dsn, schema)
        if existing:
            logger.info(
                "Using cached profile (dsn_hash=%s) — pass refresh=true to re-run",
                key,
            )
            return existing

    pool = await create_introspection_pool(dsn)
    try:
        profile = await run_introspection(
            pool=pool,
            schema=schema,
            glossary_table=glossary_table,
            dsn_hash=key,
        )
    finally:
        await pool.close()

    _save_profile_disk(profile)
    _profile_cache[key] = profile
    invalidate_agent(key)  # force rebuild with new profile
    return profile


async def _get_agent_pool(dsn: str) -> asyncpg.Pool:
    key = get_dsn_hash(dsn)
    if key not in _pool_cache:
        _pool_cache[key] = await create_agent_pool(dsn)
    return _pool_cache[key]


# ─── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    logger.info("FHI RCA Agent starting up")
    yield
    for pool in _pool_cache.values():
        await pool.close()
    logger.info("FHI RCA Agent shut down; %d pool(s) closed", len(_pool_cache))


# ─── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="FHI RCA Chat Agent",
    description=(
        "Generic Postgres root-cause-analysis assistant.  "
        "Onboard a database once via **POST /onboard**, then ask analytical "
        "questions in natural language via **POST /chat**.  "
        "The agent autonomously writes and executes read-only SQL, reasons "
        "over the results, and holds a multi-turn conversation following a "
        "structured RCA method (quantify → localize → decompose → correlate → "
        "separate observation from causation).  "
        "No frontend — integrate against this API directly."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


DEFAULT_ONBOARD_DSN: str = os.getenv("ONBOARD_DSN", "")
DEFAULT_AGENT_DSN: str = os.getenv("AGENT_DSN", "")
DEFAULT_SCHEMA: str = os.getenv("DEFAULT_SCHEMA", "public")


# ─── Request / Response schemas ───────────────────────────────────────────────


class OnboardRequest(BaseModel):
    dsn: str | None = Field(
        None,
        description="PostgreSQL connection string (DSN). If omitted, picks from ONBOARD_DSN env.",
        examples=["postgresql://user:password@localhost:5432/mydb"],
    )
    schema_name: str | None = Field(
        None,
        description="Target schema to introspect. If omitted, picks from DEFAULT_SCHEMA or 'public'.",
    )
    glossary_table: str | None = Field(
        None,
        description="Explicit table to treat as a business glossary.",
    )
    refresh: bool = Field(
        False,
        description="Force re-onboarding even if a cached profile exists.",
    )


class OnboardResponse(BaseModel):
    dsn_hash: str
    schema_name: str
    table_count: int
    fk_count: int
    glossary_entry_count: int
    is_large_schema: bool
    profiled_at: str
    profile_path: str
    message: str


class QueryUsed(BaseModel):
    sql: str
    row_count: int


class ChatRequest(BaseModel):
    session_id: str = Field(
        ...,
        description=(
            "Unique identifier for this conversation.  "
            "Reuse the same session_id across turns for multi-turn chat."
        ),
    )
    message: str = Field(..., description="User's natural-language message.")
    dsn: str | None = Field(
        None,
        description=(
            "PostgreSQL DSN for the target database.  "
            "If omitted, automatically defaults to ONBOARD_DSN or AGENT_DSN from .env."
        ),
    )
    schema_name: str | None = Field(
        None,
        description="Schema name — if omitted, defaults to DEFAULT_SCHEMA or 'public'.",
    )
    agent_dsn: str | None = Field(
        None,
        description=(
            "Optional separate DSN for the read-only agent role.  "
            "If omitted, automatically defaults to AGENT_DSN from .env."
        ),
    )


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    queries_used: list[QueryUsed]
    confidence_note: str | None = None
    message_count: int


class SessionSummary(BaseModel):
    session_id: str
    dsn_hash: str
    message_count: int
    created_at: str
    last_active: str


class ResetResponse(BaseModel):
    session_id: str
    existed: bool
    message: str


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.post(
    "/onboard",
    response_model=OnboardResponse,
    tags=["Onboarding"],
    summary="Onboard (introspect) a Postgres database",
)
async def onboard(req: OnboardRequest) -> OnboardResponse:
    """
    Introspect a Postgres schema and cache the semantic profile.

    Builds a complete picture of the database — tables, columns, primary/foreign
    keys, approximate row counts, low-cardinality filter values, date ranges for
    time columns, and any harvested business glossary — and persists it as JSON.

    Re-running this endpoint is a no-op unless **refresh=true** is passed.
    Call this endpoint before making any `/chat` requests for the same DSN.
    """
    dsn = req.dsn or DEFAULT_ONBOARD_DSN
    if not dsn:
        raise HTTPException(
            status_code=400,
            detail="No DSN provided and ONBOARD_DSN environment variable is not set.",
        )
    schema = req.schema_name or DEFAULT_SCHEMA

    try:
        profile = await _run_onboarding(
            dsn=dsn,
            schema=schema,
            glossary_table=req.glossary_table,
            refresh=req.refresh,
        )
    except asyncpg.exceptions.InvalidPasswordError:
        raise HTTPException(
            status_code=401,
            detail="Database authentication failed.  Check your credentials.",
        )
    except (
        asyncpg.exceptions.CannotConnectNowError,
        ConnectionRefusedError,
        OSError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to database: {type(exc).__name__}",
        )
    except Exception as exc:
        logger.exception("Onboarding failed")
        raise HTTPException(
            status_code=500,
            detail=f"Onboarding failed: {type(exc).__name__}: {exc}",
        )

    return OnboardResponse(
        dsn_hash=profile.dsn_hash,
        schema_name=profile.schema_name,
        table_count=len(profile.tables),
        fk_count=len(profile.foreign_keys),
        glossary_entry_count=len(profile.glossary),
        is_large_schema=profile.is_large_schema,
        profiled_at=profile.profiled_at.isoformat(),
        profile_path=profile.profile_path,
        message=(
            f"Successfully onboarded {len(profile.tables)} table(s), "
            f"{len(profile.foreign_keys)} FK relationship(s), "
            f"{len(profile.glossary)} glossary entry/entries."
        ),
    )


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["Chat"],
    summary="Send a message to the RCA agent",
)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    Ask the RCA agent a natural-language question about the database.

    The agent autonomously calls `run_sql` and `list_distinct_values` as
    needed, following the RCA method: quantify → localize → decompose →
    correlate concurrent signals → separate observation from causation.

    **Multi-turn:** pass the same `session_id` across requests.  The agent
    inherits time windows, metrics, and context from prior turns automatically.

    **Prerequisites:** call `POST /onboard` with the same DSN before the first
    chat request.  If no profile exists the endpoint returns HTTP 400 with
    instructions.

    **Response:** `answer` is the agent's prose reply; `queries_used` lists
    every SQL statement executed this turn with row counts — pipe this into a
    "how I got this" panel in the UI.
    """
    schema = req.schema_name or DEFAULT_SCHEMA
    dsn = req.dsn or DEFAULT_ONBOARD_DSN or DEFAULT_AGENT_DSN
    if not dsn:
        raise HTTPException(
            status_code=400,
            detail="No database DSN provided and neither ONBOARD_DSN nor AGENT_DSN is set in .env.",
        )

    dsn_hash = get_dsn_hash(dsn, schema)

    # ── Load profile ──────────────────────────────────────────────────────────
    profile = await _get_profile(dsn, schema)
    if profile is None:
        # Check if any cached profile exists on disk and load it if there's only one
        disk_profiles = list(PROFILES_DIR.glob("*.json"))
        if len(disk_profiles) == 1:
            try:
                candidate = _load_profile_disk(disk_profiles[0].stem)
                if candidate:
                    logger.info("Auto-loading sole cached profile: %s", candidate.dsn_hash)
                    profile = candidate
                    _profile_cache[candidate.dsn_hash] = candidate
                    dsn_hash = candidate.dsn_hash
            except Exception as e:
                logger.warning("Could not auto-load profile: %s", e)

    if profile is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No schema profile found for dsn_hash={dsn_hash!r} (schema={schema!r}).  "
                "Call POST /onboard first (or check your schema_name)."
            ),
        )

    # ── Resolve agent pool ────────────────────────────────────────────────────
    agent_dsn = req.agent_dsn or DEFAULT_AGENT_DSN or dsn
    try:
        pool = await _get_agent_pool(agent_dsn)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to agent database: {type(exc).__name__}: {exc}",
        )

    # ── Session ───────────────────────────────────────────────────────────────
    session = await session_store.get_or_create(req.session_id, dsn_hash, pool=pool)

    # ── Build per-turn deps ───────────────────────────────────────────────────
    deps = AgentDeps(
        db_pool=pool,
        schema_profile=profile,
        max_rows=MAX_ROWS,
        session_id=req.session_id,
        queries_executed=[],
    )

    # ── Run agent ─────────────────────────────────────────────────────────────
    agent = get_or_create_agent(profile)
    try:
        result = await agent.run(
            req.message,
            deps=deps,
            message_history=session.messages,
            usage_limits=get_usage_limits(),
        )
    except Exception as exc:
        logger.exception("[session=%s] Agent run failed", req.session_id)
        raise HTTPException(
            status_code=500,
            detail=f"Agent error: {type(exc).__name__}: {exc}",
        )

    # ── Persist new messages ──────────────────────────────────────────────────
    await session_store.append_messages(req.session_id, result.new_messages(), pool=pool, queries_used=deps.queries_executed)

    answer: str = getattr(result, "output", None) or getattr(result, "data", "")
    queries = [
        QueryUsed(sql=q["sql"], row_count=q["row_count"])
        for q in deps.queries_executed
    ]

    return ChatResponse(
        session_id=req.session_id,
        answer=str(answer),
        queries_used=queries,
        message_count=len(session.messages),
    )


# ─── Session management endpoints ────────────────────────────────────────────


@app.post(
    "/sessions/{session_id}/reset",
    response_model=ResetResponse,
    tags=["Sessions"],
    summary="Reset a session's conversation history",
)
async def reset_session(session_id: str) -> ResetResponse:
    """
    Clear the message history for a session so the next chat turn starts fresh.
    The session itself is retained; only the conversation is cleared.
    """
    dsn = DEFAULT_AGENT_DSN or DEFAULT_ONBOARD_DSN
    pool = await _get_agent_pool(dsn) if dsn else None
    existed = await session_store.reset(session_id, pool=pool)
    return ResetResponse(
        session_id=session_id,
        existed=existed,
        message="Conversation history cleared." if existed else "Session not found.",
    )


@app.delete(
    "/sessions/{session_id}",
    tags=["Sessions"],
    summary="Delete a session",
)
async def delete_session(session_id: str) -> dict[str, Any]:
    """Delete a session and all its message history."""
    dsn = DEFAULT_AGENT_DSN or DEFAULT_ONBOARD_DSN
    pool = await _get_agent_pool(dsn) if dsn else None
    deleted = await session_store.delete(session_id, pool=pool)
    return {"deleted": deleted, "session_id": session_id}


@app.get(
    "/sessions",
    response_model=list[SessionSummary],
    tags=["Sessions"],
    summary="List all active sessions",
)
async def list_sessions() -> list[SessionSummary]:
    """Return a summary of all in-memory / persisted sessions."""
    dsn = DEFAULT_AGENT_DSN or DEFAULT_ONBOARD_DSN
    pool = await _get_agent_pool(dsn) if dsn else None
    return [SessionSummary(**s) for s in await session_store.list_all(pool=pool)]


@app.get(
    "/sessions/{session_id}/messages",
    tags=["Sessions"],
    summary="Get UI messages for a session from database history",
)
async def get_session_messages(session_id: str) -> dict[str, Any]:
    """Return historical messages formatted for UI rendering from database."""
    dsn = DEFAULT_AGENT_DSN or DEFAULT_ONBOARD_DSN
    pool = await _get_agent_pool(dsn) if dsn else None
    msgs = await session_store.get_ui_messages(session_id, pool=pool)
    return {
        "session_id": session_id,
        "messages": msgs,
        "count": len(msgs),
    }


# ─── Schema management ────────────────────────────────────────────────────────


@app.post(
    "/schema/refresh",
    response_model=OnboardResponse,
    tags=["Onboarding"],
    summary="Force schema re-introspection",
)
async def refresh_schema(req: OnboardRequest) -> OnboardResponse:
    """
    Re-run onboarding unconditionally (equivalent to POST /onboard with refresh=true).
    Use this after database migrations or schema changes.
    """
    req_copy = req.model_copy(update={"refresh": True})
    return await onboard(req_copy)


@app.get(
    "/schema/{dsn_hash}",
    tags=["Onboarding"],
    summary="Get cached schema profile summary",
)
async def get_schema_summary(dsn_hash: str) -> dict[str, Any]:
    """Return a lightweight summary of a cached schema profile by its hash."""
    profile = _load_profile_disk(dsn_hash)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=f"No profile found for dsn_hash={dsn_hash!r}",
        )
    return profile.summary_dict()


# ─── Health ───────────────────────────────────────────────────────────────────


@app.get("/health", tags=["System"], summary="Health check")
async def health() -> dict[str, Any]:
    """Returns service status and runtime stats."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "active_sessions": len(session_store),
        "cached_profiles": len(_profile_cache),
        "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
    }
