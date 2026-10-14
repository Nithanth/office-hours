# -------- Builder stage --------
FROM python:3.11-slim AS builder

WORKDIR /app

# Ensure Python can find modules inside /app/src when executed as a package
ENV PYTHONPATH="/app/src"

# Install build deps + uv (fast Python package manager)
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Copy dependency manifests first (layer caching)
COPY pyproject.toml uv.lock ./

# Install production dependencies into a prefix dir (keeps final image slim)
RUN uv pip install --system --no-cache --prefix=/install -r pyproject.toml

# Copy application source
COPY . .

# -------- Runtime stage --------
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH="/app/src" PYTHONUNBUFFERED=1

# Create non-root user
RUN addgroup --system officehours \
    && adduser --system --ingroup officehours officehours

# Copy dependencies from builder image
COPY --from=builder /install /usr/local

# Copy application code (only runtime files)
COPY --from=builder /app /app

# Install Tesseract OCR for optional PDF image extraction
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# App writes relative dirs at runtime (data/, .cache/, .chat_cache/) — the
# non-root user must be able to create them under WORKDIR.
RUN chown -R officehours:officehours /app

USER officehours

EXPOSE 8000

# Run pending migrations before serving; migrations must stay idempotent
# because dev boots also create_all. A failed migration fails the deploy
# rather than serving a stale schema.
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
