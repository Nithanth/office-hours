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

# Install production dependencies into a prefix dir (keeps final image slim).
# --torch-backend=cpu resolves CPU-only torch wheels — without it the default
# CUDA build pulls ~5GB of nvidia-* wheels and the image export exceeds
# Railway's build deadline.
RUN uv pip install --system --no-cache --prefix=/install \
      --torch-backend=cpu -r pyproject.toml \
    && rm -rf /install/lib/python3.11/site-packages/nvidia* \
              /install/lib/python3.11/site-packages/triton* \
              /install/lib/python3.11/site-packages/torch/include \
              /install/lib/python3.11/site-packages/torch/test \
    && find /install -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find /install -type d -name "tests" -prune -exec rm -rf {} + \
    && find /install -name "*.pyc" -delete

# Copy application source
COPY . .

# Pre-download the MiniLM embedding model so runtime never depends on HF
# egress. Deps live under /install; put them on PYTHONPATH for this step.
RUN PYTHONPATH=/install/lib/python3.11/site-packages HF_HOME=/hf-cache \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# -------- Runtime stage --------
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH="/app/src" PYTHONUNBUFFERED=1
# The runtime user has no usable $HOME — point the HF/transformers cache at a
# dir it can write (otherwise model loads fail instantly → 500s).
ENV HF_HOME=/app/.cache/huggingface SENTENCE_TRANSFORMERS_HOME=/app/.cache/huggingface

# Create non-root user
RUN addgroup --system officehours \
    && adduser --system --ingroup officehours officehours

# Copy dependencies from builder image
COPY --from=builder /install /usr/local

# Copy application code (only runtime files)
COPY --from=builder /app /app

# Baked embedding model (see builder stage)
COPY --from=builder /hf-cache /app/.cache/huggingface

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
