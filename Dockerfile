# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv directly via pip (avoids COPY --from ghcr.io which can hit
# credential helper issues in some Docker setups)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (layer cache)
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends rsync && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the venv from builder (includes hf_serve as a proper installed package)
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["hf-serve"]
CMD ["--config", "/etc/hf-serve/config.yaml", "serve", "--host", "0.0.0.0", "--port", "8080"]
