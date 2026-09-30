"""
Acceptance tests for the FHI RCA Agent (§8 of the build spec).

These are integration tests that drive a live server through the two
acceptance scenarios.  They are skipped automatically when TEST_DSN is not
set so CI doesn't fail without a database.

Prerequisites
-------------
1. A running FHI RCA Agent server:
       uv run uvicorn app.main:app --reload

2. Environment variables:
       TEST_DSN        — PostgreSQL DSN for onboarding (schema owner / superuser)
       TEST_AGENT_DSN  — DSN for the read-only agent role (defaults to TEST_DSN)
       TEST_SCHEMA     — Schema name (default: public)
       TEST_BASE_URL   — Server base URL (default: http://localhost:8000)

Run
---
    # With a live database + server:
    TEST_DSN="postgresql://..." uv run pytest tests/test_acceptance.py -v

    # Without a database (skips integration tests, runs unit tests only):
    uv run pytest tests/ -v
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
TEST_DSN = os.getenv("TEST_DSN", "")
TEST_AGENT_DSN = os.getenv("TEST_AGENT_DSN", TEST_DSN)
TEST_SCHEMA = os.getenv("TEST_SCHEMA", "public")

# ─── Marks ───────────────────────────────────────────────────────────────────

needs_server = pytest.mark.skipif(
    not TEST_DSN,
    reason="TEST_DSN not set — skipping integration test",
)

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _chat(client: httpx.Client, session_id: str, message: str) -> dict:
    """POST /chat and return parsed JSON response."""
    resp = client.post(
        f"{BASE_URL}/chat",
        json={
            "session_id": session_id,
            "message": message,
            "dsn": TEST_DSN,
            "agent_dsn": TEST_AGENT_DSN or TEST_DSN,
            "schema_name": TEST_SCHEMA,
        },
        timeout=120.0,
    )
    assert resp.status_code == 200, (
        f"POST /chat returned {resp.status_code}: {resp.text[:500]}"
    )
    return resp.json()


def _onboard(client: httpx.Client) -> dict:
    """POST /onboard and return parsed JSON response."""
    resp = client.post(
        f"{BASE_URL}/onboard",
        json={"dsn": TEST_DSN, "schema_name": TEST_SCHEMA},
        timeout=300.0,
    )
    assert resp.status_code == 200, (
        f"POST /onboard returned {resp.status_code}: {resp.text[:500]}"
    )
    return resp.json()


# ─── Unit tests (no DB needed) ────────────────────────────────────────────────


class TestSQLGuard:
    """Validate the sqlglot guard without any database."""

    def test_select_passes(self):
        from app.sql_guard import validate_and_limit_sql
        sql = validate_and_limit_sql("SELECT 1")
        assert "SELECT" in sql.upper()

    def test_limit_injected(self):
        from app.sql_guard import validate_and_limit_sql
        sql = validate_and_limit_sql("SELECT id FROM orders", max_rows=250)
        assert "250" in sql or "LIMIT" in sql.upper()

    def test_existing_limit_respected(self):
        from app.sql_guard import validate_and_limit_sql
        sql = validate_and_limit_sql("SELECT id FROM orders LIMIT 10", max_rows=500)
        # Existing limit ≤ max_rows — should be preserved
        assert "10" in sql

    def test_large_limit_capped(self):
        from app.sql_guard import validate_and_limit_sql
        sql = validate_and_limit_sql("SELECT id FROM orders LIMIT 9999", max_rows=500)
        # Capped at max_rows
        assert "9999" not in sql

    def test_insert_rejected(self):
        from app.sql_guard import SQLValidationError, validate_and_limit_sql
        with pytest.raises(SQLValidationError, match="INSERT"):
            validate_and_limit_sql("INSERT INTO foo VALUES (1)")

    def test_update_rejected(self):
        from app.sql_guard import SQLValidationError, validate_and_limit_sql
        with pytest.raises(SQLValidationError):
            validate_and_limit_sql("UPDATE orders SET status = 'closed'")

    def test_delete_rejected(self):
        from app.sql_guard import SQLValidationError, validate_and_limit_sql
        with pytest.raises(SQLValidationError):
            validate_and_limit_sql("DELETE FROM orders")

    def test_drop_rejected(self):
        from app.sql_guard import SQLValidationError, validate_and_limit_sql
        with pytest.raises(SQLValidationError):
            validate_and_limit_sql("DROP TABLE orders")

    def test_multiple_statements_rejected(self):
        from app.sql_guard import SQLValidationError, validate_and_limit_sql
        with pytest.raises(SQLValidationError, match="[Mm]ultiple"):
            validate_and_limit_sql("SELECT 1; SELECT 2")

    def test_cte_allowed(self):
        from app.sql_guard import validate_and_limit_sql
        sql = validate_and_limit_sql(
            "WITH cte AS (SELECT id FROM orders) SELECT * FROM cte"
        )
        assert "WITH" in sql.upper() or "SELECT" in sql.upper()

    def test_empty_rejected(self):
        from app.sql_guard import SQLValidationError, validate_and_limit_sql
        with pytest.raises(SQLValidationError):
            validate_and_limit_sql("")


class TestSchemaProfileRendering:
    """Test that the schema profile renders usable text."""

    def test_render_contains_table_names(self):
        from datetime import datetime, timezone
        from app.schema_profile import (
            SchemaProfile, TableInfo, ColumnInfo, ForeignKey
        )
        profile = SchemaProfile(
            dsn_hash="abc123",
            schema_name="public",
            profiled_at=datetime.now(timezone.utc),
            tables=[
                TableInfo(
                    name="orders",
                    table_type="BASE TABLE",
                    row_count=1000,
                    columns=[
                        ColumnInfo(
                            name="id",
                            data_type="integer",
                            is_nullable=False,
                        ),
                        ColumnInfo(
                            name="status",
                            data_type="character varying",
                            is_nullable=True,
                            distinct_values=["open", "closed", "pending"],
                        ),
                        ColumnInfo(
                            name="created_at",
                            data_type="timestamp without time zone",
                            is_nullable=False,
                            date_min="2024-01-01",
                            date_max="2024-12-31",
                        ),
                    ],
                    primary_keys=["id"],
                )
            ],
            foreign_keys=[],
            glossary=[],
        )
        rendered = profile.render_for_prompt()
        assert "orders" in rendered
        assert "1,000" in rendered
        assert "open" in rendered
        assert "2024-01-01" in rendered
        assert "PK" in rendered

    def test_render_join_graph(self):
        from datetime import datetime, timezone
        from app.schema_profile import (
            SchemaProfile, TableInfo, ColumnInfo, ForeignKey
        )
        profile = SchemaProfile(
            dsn_hash="xyz",
            schema_name="public",
            profiled_at=datetime.now(timezone.utc),
            tables=[
                TableInfo(name="orders", table_type="BASE TABLE",
                          columns=[ColumnInfo(name="store_id", data_type="integer", is_nullable=False)]),
                TableInfo(name="stores", table_type="BASE TABLE",
                          columns=[ColumnInfo(name="id", data_type="integer", is_nullable=False)]),
            ],
            foreign_keys=[
                ForeignKey(from_table="orders", from_column="store_id",
                           to_table="stores", to_column="id")
            ],
            glossary=[],
        )
        rendered = profile.render_for_prompt()
        assert "JOIN GRAPH" in rendered
        assert "orders.store_id" in rendered
        assert "stores.id" in rendered


class TestDsnHash:
    """Test DSN hashing is stable and credential-free."""

    def test_same_dsn_same_hash(self):
        from app.db import get_dsn_hash
        h1 = get_dsn_hash("postgresql://admin:secret@localhost:5432/mydb", "public")
        h2 = get_dsn_hash("postgresql://admin:secret@localhost:5432/mydb", "public")
        assert h1 == h2

    def test_different_schema_different_hash(self):
        from app.db import get_dsn_hash
        h1 = get_dsn_hash("postgresql://admin:secret@localhost:5432/mydb", "public")
        h2 = get_dsn_hash("postgresql://admin:secret@localhost:5432/mydb", "reporting")
        assert h1 != h2

    def test_hash_does_not_contain_password(self):
        from app.db import get_dsn_hash
        h = get_dsn_hash("postgresql://admin:supersecret@localhost:5432/mydb", "public")
        assert "supersecret" not in h


class TestRedactDsn:
    """Ensure DSN redaction works correctly."""

    def test_password_redacted(self):
        from app.db import redact_dsn
        safe = redact_dsn("postgresql://user:mypassword@host/db")
        assert "mypassword" not in safe
        assert "***" in safe

    def test_no_password_unchanged(self):
        from app.db import redact_dsn
        dsn = "postgresql://host/db"
        assert redact_dsn(dsn) == dsn


# ─── Integration tests — Scenario 1: Concurrent signal alert ─────────────────


@needs_server
class TestScenario1ConcurrentSignals:
    """
    §8 Scenario 1 — Three concurrent alert signals.

    Pass criteria:
    - Agent names all three signals (sales gap, SKU inventory, contractor accounts)
    - Ties them to the same store / time window
    - Explicitly states the data does NOT establish causation between signals
    """

    def test_concurrent_signals_all_named(self):
        session_id = f"s1-{uuid.uuid4().hex[:8]}"

        with httpx.Client() as client:
            # Ensure onboarded
            _onboard(client)

            response = _chat(
                client,
                session_id,
                "Three alerts have appeared: decking sales are below expected "
                "pace; two decking SKUs are low on inventory; three regular "
                "contractor accounts have reduced their purchases.  "
                "What's going on?",
            )

        answer = response["answer"].lower()

        # ── Must name all three signals ──────────────────────────────────────
        assert any(kw in answer for kw in ["sales", "revenue", "pace"]), (
            "Answer must mention the sales/pace signal"
        )
        assert any(kw in answer for kw in ["inventory", "stock", "sku"]), (
            "Answer must mention the inventory signal"
        )
        assert any(kw in answer for kw in ["contractor", "account", "customer"]), (
            "Answer must mention the contractor account signal"
        )

        # ── Must NOT assert causation ────────────────────────────────────────
        CAUSATION = [
            "inventory caused",
            "caused the sales",
            "caused by inventory",
            "caused by low stock",
            "the reason is",
            "because of low inventory",
        ]
        for phrase in CAUSATION:
            assert phrase not in answer, (
                f"Answer must not assert causation (found forbidden phrase: {phrase!r})"
            )

        # ── Must hedge / signal uncertainty ─────────────────────────────────
        HEDGING = [
            "cannot", "can't", "unclear", "correlation",
            "investigate", "doesn't tell", "further", "possible",
            "may ", "might", "could", "appear", "suggest",
            "not yet establish", "not establish",
        ]
        assert any(kw in answer for kw in HEDGING), (
            "Answer must acknowledge causal uncertainty with hedging language"
        )

        # ── Must have executed SQL ───────────────────────────────────────────
        assert len(response["queries_used"]) >= 1, (
            "Agent must run at least one SQL query to investigate the alerts"
        )

    def test_concurrent_signals_queries_logged(self):
        """Every /chat response must include queries_used."""
        session_id = f"s1b-{uuid.uuid4().hex[:8]}"
        with httpx.Client() as client:
            _onboard(client)
            resp = _chat(client, session_id, "What are the top 5 product categories by sales?")
        assert "queries_used" in resp
        assert isinstance(resp["queries_used"], list)


# ─── Integration tests — Scenario 2: Multi-turn Monday review ────────────────


@needs_server
class TestScenario2MondayReview:
    """
    §8 Scenario 2 — Eight-turn Monday morning management review.

    Pass criteria:
    - Follow-ups inherit context (time window, metric) without re-stating
    - "Driving it" answer uses correlation-only language (no causation)
    - "Known vs unknown" turn cleanly separates facts from open questions
    - Closing "three things" are specific and traceable to named entities
    """

    @pytest.fixture(scope="class")
    def monday_session(self):
        """Shared session fixture: run all eight turns and return responses."""
        session_id = f"s2-{uuid.uuid4().hex[:8]}"
        responses: dict[str, dict] = {}

        turns = [
            ("t1", "Morning.  How did we do against plan last week?"),
            ("t2", "That's not great, where did we lose the most?"),
            ("t3", "Which stores are dragging us down?"),
            ("t4", "What's happening with Lumber and Decking?"),
            ("t5", "Is this something that just happened last week, or has it been building?"),
            ("t6", "What do you think is driving it?"),
            ("t7", "Can you separate what we know from what we still need to investigate?"),
            ("t8", "Give me three things to discuss with my store managers today."),
        ]

        with httpx.Client() as client:
            _onboard(client)
            for key, msg in turns:
                responses[key] = _chat(client, session_id, msg)

        return responses

    def test_turn6_no_causation_assertion(self, monday_session):
        """'What's driving it?' must stay in correlation-only language."""
        answer = monday_session["t6"]["answer"].lower()

        CAUSATION = [
            "is causing", "caused by", "the cause is",
            "the reason is", "because of", "due to the fact",
        ]
        for phrase in CAUSATION:
            assert phrase not in answer, (
                f"Turn 6 must not assert causation (found: {phrase!r})"
            )

        CORRELATION = [
            "correlat", "associat", "suggest", "may ", "might",
            "could", "appears", "seems", "possible", "potential",
            "further", "coincid",
        ]
        assert any(kw in answer for kw in CORRELATION), (
            "Turn 6 must use hedging / correlation language"
        )

    def test_turn7_separates_known_from_unknown(self, monday_session):
        """'Separate known from unknown' must present both categories."""
        answer = monday_session["t7"]["answer"].lower()

        KNOWN_SIGNALS = [
            "know", "observed", "data show", "confirmed",
            "measured", "we can see", "established",
        ]
        UNKNOWN_SIGNALS = [
            "unknown", "unclear", "don't know", "need to investigate",
            "open question", "hypothesis", "cannot confirm", "can't confirm",
            "not yet", "still need",
        ]
        assert any(kw in answer for kw in KNOWN_SIGNALS), (
            "Turn 7 must identify what is known / observed"
        )
        assert any(kw in answer for kw in UNKNOWN_SIGNALS), (
            "Turn 7 must identify what remains unknown / open"
        )

    def test_turn8_three_specific_items(self, monday_session):
        """'Three things for store managers' must be numbered and specific."""
        answer = monday_session["t8"]["answer"].lower()

        # Structured as ≥3 items
        NUMBER_SIGNALS = [
            "1.", "2.", "3.",
            "first,", "second,", "third,",
            "first:", "second:", "third:",
            "one:", "two:", "three:",
        ]
        assert any(kw in answer for kw in NUMBER_SIGNALS), (
            "Turn 8 must present at least three numbered/structured items"
        )

        # Must reference specific entities mentioned in the conversation
        SPECIFIC_ENTITIES = [
            "lumber", "decking", "store", "category", "sku",
            "inventory", "contractor", "week",
        ]
        assert any(kw in answer for kw in SPECIFIC_ENTITIES), (
            "Turn 8 must reference specific entities from the conversation, not generic advice"
        )

    def test_later_turns_ran_queries(self, monday_session):
        """Investigative follow-up turns must have executed SQL queries."""
        for key in ("t2", "t3", "t4", "t5"):
            assert len(monday_session[key]["queries_used"]) >= 1, (
                f"Turn {key} must run SQL queries (context inheritance requires data)"
            )

    def test_message_count_grows(self, monday_session):
        """message_count should grow with each turn (history accumulates)."""
        counts = [monday_session[f"t{i}"]["message_count"] for i in range(1, 9)]
        for i in range(1, len(counts)):
            assert counts[i] > counts[i - 1], (
                f"Message count did not grow between turn {i} and {i+1}: "
                f"{counts[i-1]} → {counts[i]}"
            )


# ─── Integration: session management ─────────────────────────────────────────


@needs_server
class TestSessionManagement:
    """Test reset and delete session endpoints."""

    def test_reset_clears_history(self):
        session_id = f"reset-{uuid.uuid4().hex[:8]}"
        with httpx.Client() as client:
            _onboard(client)
            _chat(client, session_id, "Hello")
            _chat(client, session_id, "What tables do you see?")

            # Reset
            resp = client.post(
                f"{BASE_URL}/sessions/{session_id}/reset"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["existed"] is True

            # New turn after reset — message_count should restart low
            next_resp = _chat(client, session_id, "Hello again")
            assert next_resp["message_count"] <= 4  # only the new turn's messages

    def test_delete_session(self):
        session_id = f"del-{uuid.uuid4().hex[:8]}"
        with httpx.Client() as client:
            _onboard(client)
            _chat(client, session_id, "Hi")

            resp = client.delete(f"{BASE_URL}/sessions/{session_id}")
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True


# ─── Integration: health check ────────────────────────────────────────────────


@needs_server
def test_health_endpoint():
    with httpx.Client() as client:
        resp = client.get(f"{BASE_URL}/health", timeout=10)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
