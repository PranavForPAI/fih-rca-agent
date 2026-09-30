"""
Postgres-backed (with in-memory fallback) multi-turn session store.

Conversations are stored in a dedicated database schema (default: fhi_chat)
separate from the onboarded business database schema (e.g. fhi_sales).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg
from pydantic_core import to_jsonable_python
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """State for a single multi-turn chat conversation."""

    session_id: str
    dsn_hash: str
    messages: list[ModelMessage] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_active: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def touch(self) -> None:
        self.last_active = datetime.now(timezone.utc)


class SessionStore:
    """
    Session store supporting both PostgreSQL persistence (schema fhi_chat)
    and transparent memory fallback.
    """

    def __init__(self, chat_schema: str = "fhi_chat") -> None:
        self._sessions: dict[str, ChatSession] = {}
        self.chat_schema = chat_schema

    async def get_or_create(
        self, session_id: str, dsn_hash: str, pool: asyncpg.Pool | None = None
    ) -> ChatSession:
        """Return existing session or create a new one, hydrating from DB if available."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            return session

        session = ChatSession(session_id=session_id, dsn_hash=dsn_hash)

        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        f"""
                        INSERT INTO {self.chat_schema}.chat_sessions (session_id, dsn_hash, last_active)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (session_id) DO UPDATE
                            SET last_active = NOW()
                        RETURNING created_at, last_active;
                        """,
                        session_id,
                        dsn_hash,
                    )
                    if row:
                        session.created_at = row["created_at"]
                        session.last_active = row["last_active"]

                    records = await conn.fetch(
                        f"""
                        SELECT raw_payload FROM {self.chat_schema}.chat_messages
                        WHERE session_id = $1
                        ORDER BY id ASC;
                        """,
                        session_id,
                    )
                    for rec in records:
                        if rec["raw_payload"]:
                            try:
                                payload = rec["raw_payload"]
                                if isinstance(payload, str):
                                    payload = json.loads(payload)
                                if isinstance(payload, list):
                                    msgs = ModelMessagesTypeAdapter.validate_python(payload)
                                    session.messages.extend(msgs)
                                else:
                                    msg = ModelMessagesTypeAdapter.validate_python([payload])
                                    session.messages.extend(msg)
                            except Exception as parse_err:
                                logger.warning(
                                    "[session=%s] Failed parsing raw_payload: %s",
                                    session_id,
                                    parse_err,
                                )
            except Exception as exc:
                logger.warning(
                    "[session=%s] Could not load session from postgres (%s): %s",
                    session_id,
                    self.chat_schema,
                    exc,
                )

        self._sessions[session_id] = session
        return session

    async def append_messages(
        self,
        session_id: str,
        new_messages: list[ModelMessage],
        pool: asyncpg.Pool | None = None,
        queries_used: list[dict[str, Any]] | None = None,
    ) -> None:
        """Extend session message history and write records to DB."""
        session = self._sessions.get(session_id)
        if session is not None:
            session.messages.extend(new_messages)
            session.touch()

        if pool is not None and new_messages:
            try:
                jsonable = to_jsonable_python(new_messages)
                async with pool.acquire() as conn:
                    await conn.execute(
                        f"""
                        UPDATE {self.chat_schema}.chat_sessions
                        SET last_active = NOW()
                        WHERE session_id = $1;
                        """,
                        session_id,
                    )
                    serialized_queries = json.dumps(queries_used or [])
                    serialized_payload = json.dumps(jsonable)
                    turn_text = ""
                    for m in new_messages:
                        turn_text += str(getattr(m, "content", "") or "")
                    await conn.execute(
                        f"""
                        INSERT INTO {self.chat_schema}.chat_messages
                            (session_id, role, content, queries_used, raw_payload)
                        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb);
                        """,
                        session_id,
                        "turn",
                        turn_text[:1000],
                        serialized_queries,
                        serialized_payload,
                    )
            except Exception as exc:
                logger.warning(
                    "[session=%s] Failed saving messages to postgres: %s",
                    session_id,
                    exc,
                )

    async def reset(self, session_id: str, pool: asyncpg.Pool | None = None) -> bool:
        """Clear conversation history for a session."""
        existed = session_id in self._sessions
        session = self._sessions.get(session_id)
        if session:
            session.messages = []
            session.touch()

        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    res = await conn.execute(
                        f"DELETE FROM {self.chat_schema}.chat_messages WHERE session_id = $1;",
                        session_id,
                    )
                    if res and not res.endswith("0"):
                        existed = True
            except Exception as exc:
                logger.warning("Failed resetting session in db: %s", exc)

        return existed

    async def delete(self, session_id: str, pool: asyncpg.Pool | None = None) -> bool:
        """Delete a session entirely."""
        existed = session_id in self._sessions
        self._sessions.pop(session_id, None)

        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    res = await conn.execute(
                        f"DELETE FROM {self.chat_schema}.chat_sessions WHERE session_id = $1;",
                        session_id,
                    )
                    if res and not res.endswith("0"):
                        existed = True
            except Exception as exc:
                logger.warning("Failed deleting session in db: %s", exc)

        return existed

    async def list_all(self, pool: asyncpg.Pool | None = None) -> list[dict[str, Any]]:
        """Return summary dicts for all sessions."""
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        f"""
                        SELECT s.session_id, s.dsn_hash, s.created_at, s.last_active,
                               COUNT(m.id) AS msg_count
                        FROM {self.chat_schema}.chat_sessions s
                        LEFT JOIN {self.chat_schema}.chat_messages m ON s.session_id = m.session_id
                        GROUP BY s.session_id, s.dsn_hash, s.created_at, s.last_active
                        ORDER BY s.last_active DESC;
                        """
                    )
                    return [
                        {
                            "session_id": r["session_id"],
                            "dsn_hash": r["dsn_hash"],
                            "message_count": int(r["msg_count"]),
                            "created_at": r["created_at"].isoformat(),
                            "last_active": r["last_active"].isoformat(),
                        }
                        for r in rows
                    ]
            except Exception as exc:
                logger.warning("Failed listing sessions from db: %s", exc)

        return [
            {
                "session_id": s.session_id,
                "dsn_hash": s.dsn_hash,
                "message_count": len(s.messages),
                "created_at": s.created_at.isoformat(),
                "last_active": s.last_active.isoformat(),
            }
            for s in self._sessions.values()
        ]

    async def get_ui_messages(
        self, session_id: str, pool: asyncpg.Pool | None = None
    ) -> list[dict[str, Any]]:
        """Return UI messages (with role, parts, and queries breakdown) for frontend rendering."""
        ui_msgs: list[dict[str, Any]] = []

        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, queries_used, raw_payload
                        FROM {self.chat_schema}.chat_messages
                        WHERE session_id = $1
                        ORDER BY id ASC;
                        """,
                        session_id,
                    )
                    for r in rows:
                        rid = r["id"]
                        raw = r["raw_payload"]
                        payload = json.loads(raw) if isinstance(raw, str) else (raw or [])
                        queries = r["queries_used"]
                        if isinstance(queries, str):
                            try:
                                queries = json.loads(queries)
                            except Exception:
                                queries = []

                        user_text = ""
                        asst_text = ""
                        for item in payload:
                            if item.get("kind") == "request":
                                for p in item.get("parts", []):
                                    if p.get("part_kind") == "user-prompt":
                                        content = p.get("content", "")
                                        if isinstance(content, str):
                                            user_text = content
                            elif item.get("kind") == "response":
                                for p in item.get("parts", []):
                                    if p.get("part_kind") == "text":
                                        content = p.get("content", "")
                                        if isinstance(content, str):
                                            asst_text = content

                        if user_text:
                            ui_msgs.append({
                                "id": f"msg-u-{rid}",
                                "role": "user",
                                "parts": [{"type": "text", "text": user_text}],
                            })
                        if asst_text:
                            full_text = asst_text
                            if queries and len(queries) > 0:
                                q_list = "\n\n".join([
                                    f"**Query {i+1}** ({q.get('row_count', 0)} rows):\n```sql\n{q.get('sql', '')}\n```"
                                    for i, q in enumerate(queries)
                                ])
                                full_text += f"\n\n<details>\n<summary>🔍 <b>Queries Executed ({len(queries)})</b></summary>\n\n{q_list}\n</details>"
                            ui_msgs.append({
                                "id": f"msg-a-{rid}",
                                "role": "assistant",
                                "parts": [{"type": "text", "text": full_text}],
                            })
            except Exception as exc:
                logger.warning(
                    "[session=%s] Failed fetching ui_messages from postgres: %s",
                    session_id,
                    exc,
                )

        return ui_msgs

    def __len__(self) -> int:
        return len(self._sessions)


session_store = SessionStore()
