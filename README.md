# FHI RCA Chat Agent

A conversational **root-cause-analysis (RCA) assistant** that onboards any Postgres database and answers analytical questions like a data-literate analyst — quantifying gaps, localizing where they concentrate, decomposing metrics, correlating signals, and always separating observed facts from unproven hypotheses.

Built with **Pydantic AI**, **FastAPI**, **asyncpg**, and **sqlglot**.  No frontend — this is a pure API, documented via the auto-generated OpenAPI spec at `/docs`.

---

## Architecture

```
fhi-bot/
├── onboard.py            # CLI: introspect DB → write schema_profile.json
├── app/
│   ├── main.py           # FastAPI: /onboard, /chat, session & schema endpoints
│   ├── agent.py          # Pydantic AI Agent, system prompt, run_sql / list_distinct_values tools
│   ├── db.py             # asyncpg pool factories + DSN utilities
│   ├── introspection.py  # information_schema / pg_catalog crawl + column profiling
│   ├── sql_guard.py      # sqlglot validation + LIMIT injection
│   ├── schema_profile.py # Pydantic models: SchemaProfile, TableInfo, ColumnInfo, …
│   └── sessions.py       # In-memory multi-turn message-history store
├── schema_profiles/      # Cached JSON profiles (gitignored)
└── tests/
    └── test_acceptance.py # Unit tests (no DB) + §8 acceptance scenarios
```

**Data flow for a chat turn:**

```
POST /chat
  → load SchemaProfile (disk cache → memory)
  → get_or_create_agent(profile)          ← one Agent per dsn_hash, cached
  → agent.run(message, message_history)   ← Pydantic AI tool-calling loop
       ↳ run_sql() → sqlglot validate → asyncpg execute (read-only role)
       ↳ list_distinct_values() → asyncpg execute
       ↳ … (up to MAX_TOOL_CALLS round-trips)
  → persist result.new_messages() to session
  → return { answer, queries_used, message_count }
```

---

## Quick start

### 1. Install (uv)

```bash
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set GEMINI_API_KEY (or ANTHROPIC_API_KEY)
```

Key variables:

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | _(auto-detect)_ | `gemini` or `anthropic` |
| `GEMINI_API_KEY` | _(optional)_ | Your Google Gemini API key (or `GOOGLE_API_KEY`) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `ANTHROPIC_API_KEY` | _(optional)_ | Your Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-3-5-sonnet-20241022` | Anthropic model name |
| `MAX_ROWS` | `500` | Max rows returned per SQL query |
| `MAX_TOOL_CALLS` | `20` | Max LLM round-trips per chat turn |
| `PROFILES_DIR` | `schema_profiles` | Directory for cached JSON profiles |
| `LOG_LEVEL` | `info` | Uvicorn / app log level |

### 3. Set up the read-only database role

The agent pool uses a dedicated read-only role.  Run this once as a superuser
on your Postgres server:

```sql
CREATE ROLE rca_agent LOGIN PASSWORD 'choose-a-strong-password' NOSUPERUSER;

-- Grant on the target schema (repeat for each schema you onboard)
GRANT USAGE  ON SCHEMA public TO rca_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO rca_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO rca_agent;

-- Harden at role level
ALTER ROLE rca_agent SET statement_timeout        = '10s';
ALTER ROLE rca_agent SET default_transaction_read_only = on;
```

Set your `.env`:
```
# Onboarding DSN (schema owner / superuser)
ONBOARD_DSN=postgresql://postgres:adminpass@localhost:5432/mydb
# Agent DSN (read-only role)
AGENT_DSN=postgresql://rca_agent:choose-a-strong-password@localhost:5432/mydb
```

### 4. Onboard the database (once)

```bash
# Basic
uv run python onboard.py --dsn "postgresql://postgres:pass@localhost:5432/mydb"

# With schema + explicit glossary table + verbose
uv run python onboard.py \
  --dsn "postgresql://postgres:pass@localhost:5432/mydb" \
  --schema reporting \
  --glossary-table dataset_note \
  --verbose

# Force refresh after a migration
uv run python onboard.py --dsn "..." --refresh
```

Sample output:
```
🔍  Connecting to: postgresql://postgres:***@localhost:5432/mydb
    Schema:  public

⏳  Introspecting schema … done.
✅  Profile written to: schema_profiles/a3f7c9b1d2e4f6a8.json

──────────────────────────────────────────────────────────────
  Schema:           public
  Profiled:         2024-01-15 09:23 UTC
  Tables:           18
  FK relationships: 12
  Glossary entries: 45
  Large schema:     False

  Date ranges (3 columns):
    sales.sale_date: 2023-01-01 → 2024-01-14
    orders.order_date: 2023-01-01 → 2024-01-14
    inventory.recorded_at: 2023-06-01 → 2024-01-14

  Tables (18):
    accounts                        5,234 rows    8 cols   PK: id
    categories                         24 rows    4 cols   PK: id
    ...
──────────────────────────────────────────────────────────────
```

### 5. Start the server

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

OpenAPI docs: **http://localhost:8000/docs**

---

## API Reference

### `POST /onboard`

Introspect a database and cache its schema profile.  Re-running is a no-op
unless `refresh: true`.

```json
{
  "dsn": "postgresql://postgres:pass@localhost:5432/mydb",
  "schema_name": "public",
  "glossary_table": null,
  "refresh": false
}
```

### `POST /chat`

Send a message to the RCA agent.  Pass the same `session_id` across turns for
multi-turn conversation.

```json
{
  "session_id": "session-abc123",
  "message": "How did we do against plan last week?",
  "dsn": "postgresql://postgres:pass@localhost:5432/mydb",
  "agent_dsn": "postgresql://rca_agent:agentpass@localhost:5432/mydb",
  "schema_name": "public"
}
```

Response:
```json
{
  "session_id": "session-abc123",
  "answer": "Last week's total sales came in at $1.24M against a plan of $1.41M — a shortfall of $170K (–12%).  The gap was concentrated in the South region, which ran $115K below plan ...",
  "queries_used": [
    {
      "sql": "SELECT SUM(amount) AS actual, ... FROM sales WHERE sale_date >= ...",
      "row_count": 7
    }
  ],
  "message_count": 2
}
```

### Session management

| Method | Endpoint | Action |
|---|---|---|
| `POST` | `/sessions/{id}/reset` | Clear conversation history |
| `DELETE` | `/sessions/{id}` | Delete session entirely |
| `GET` | `/sessions` | List all active sessions |

### Schema management

| Method | Endpoint | Action |
|---|---|---|
| `POST` | `/schema/refresh` | Force re-onboarding (same as `/onboard` + `refresh=true`) |
| `GET` | `/schema/{dsn_hash}` | Get cached profile summary |

---

## Testing

### Unit tests (no database required)

```bash
uv run pytest tests/test_acceptance.py -v -k "not needs_server"
```

Tests `SQLGuard`, `SchemaProfile` rendering, DSN hashing, and DSN redaction.

### Acceptance tests (§8 — requires live DB + running server)

```bash
# Terminal 1: start server
uv run uvicorn app.main:app --reload

# Terminal 2: run acceptance tests
TEST_DSN="postgresql://postgres:pass@localhost:5432/mydb" \
TEST_AGENT_DSN="postgresql://rca_agent:agentpass@localhost:5432/mydb" \
uv run pytest tests/test_acceptance.py -v
```

**Scenario 1 — Concurrent signal alert**
> "Three alerts: decking sales below pace, two SKUs low on inventory, three contractor accounts reduced purchases."

Pass criteria: agent names all three signals, ties them to a store/window, explicitly does **not** assert that low inventory caused the sales drop.

**Scenario 2 — Eight-turn Monday review**
> Morning plan review → where did we lose → which stores → lumber/decking → trend → driving it → known vs unknown → three things for managers.

Pass criteria: every follow-up inherits context; "driving it" answer uses correlation-only language; "known vs unknown" cleanly separates facts from open questions; "three things" are specific and traceable to numbers from the conversation.

---

## Guardrails

| Guardrail | Implementation |
|---|---|
| Read-only DB access | `default_transaction_read_only = on` at role level |
| Statement timeout | `statement_timeout = '10s'` at role level |
| SQL validation | sqlglot rejects all non-`SELECT` statements before execution |
| Auto-LIMIT | Injected if absent; capped at `MAX_ROWS` (default 500) |
| Bounded loops | `UsageLimits(request_limit=MAX_TOOL_CALLS)` per chat turn |
| Credential safety | DSN password redacted in all logs and error messages |
| SQL audit log | Every executed statement logged at INFO with session ID |

---

## Extending

**Add a new LLM provider**
Change `ANTHROPIC_MODEL` in `.env` and update `create_agent()` in `app/agent.py`
to use a different `pydantic_ai.models.*` class (e.g. `OpenAIModel`).

**Persistent session store**
Replace `SessionStore` in `app/sessions.py` with a Redis-backed implementation
following the same interface (`get`, `get_or_create`, `append_messages`, `reset`, `delete`).

**Large schemas (> 100 tables)**
`SchemaProfile.is_large_schema` is set to `True` automatically.  Add a
`search_schema(question: str)` tool in `app/agent.py` backed by a ChromaDB or
pgvector index built during onboarding — see §2.7 of the build spec.

**Schema-specific curated tools**
For a specific database deployment, add optional tools (e.g. `get_plan_vs_actual`)
as an extra toolset selected by a config flag — without changing the generic base.
