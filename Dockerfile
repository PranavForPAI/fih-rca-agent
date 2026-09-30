# ─── Stage 1: builder ─────────────────────────────────────────────────────────
# Uses uv to resolve and install dependencies into an isolated layer.
# Keeping the build stage separate means the final image doesn't carry
# the uv binary, pip cache, or any build-time tooling.
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /build

# Copy dependency manifests first so Docker can cache the pip install layer
# independently of source-code changes.
COPY pyproject.toml uv.lock ./

# Sync dependencies into a local .venv (no editable install of the project yet)
RUN uv sync --frozen --no-install-project --no-dev

# Now copy source and install the project itself
COPY app/ ./app/
RUN uv sync --frozen --no-dev


# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN addgroup --system fhibot && adduser --system --ingroup fhibot fhibot

WORKDIR /app

# Copy the fully resolved virtual environment from the builder
COPY --from=builder /build/.venv /app/.venv

# Copy application source
COPY --from=builder /build/app ./app

# Schema profiles directory (mounted as a volume in production)
RUN mkdir -p /app/schema_profiles && chown fhibot:fhibot /app/schema_profiles

# Make the venv's bin the first entry on PATH so uvicorn / python resolve correctly
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROFILES_DIR=/app/schema_profiles

USER fhibot

EXPOSE 8000

# Health check — FastAPI serves GET /health
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
