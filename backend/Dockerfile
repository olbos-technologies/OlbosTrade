# ── Build stage ────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a separate layer for caching
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash olbostrade

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=olbostrade:olbostrade . .

# The commit this image is built from (update.sh passes it). Reported on
# /api/health/detail and in the startup log; unset reads as "unknown".
ARG GIT_SHA=""
ENV GIT_SHA=$GIT_SHA

# Create directories the app writes to
RUN mkdir -p /app/ml/model_registry /app/logs \
    && chown -R olbostrade:olbostrade /app/ml /app/logs

USER olbostrade

EXPOSE 8000

# Health check built into image
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production: no --reload. MUST be a single worker — the app is inherently
# single-instance. It holds ONE stateful IBKR socket (IBKR permits one
# connection per client id) and runs ONE in-process trading scheduler
# (asyncio.create_task in the lifespan). With >1 worker each process opens a
# competing IBKR connection (so connection status flaps "connected" on one
# worker / "disconnected" on another across requests) and the trading agent
# ticks once per worker, risking double-executed orders. Concurrency is handled
# via asyncio within this single process, which is ample for this workload.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--access-log"]
