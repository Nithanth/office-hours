# -------- Builder stage --------
FROM python:3.10-slim AS builder

WORKDIR /app

# Ensure Python can find modules inside /app/src when executed as a package
ENV PYTHONPATH="/app/src"

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

# Copy dependency list & install into a temp dir (keeps final image slim)
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install --no-warn-script-location --no-cache-dir --prefix=/install -r requirements.txt

# Copy application source (needed for static analysis & runtime)
COPY . .

# -------- Runtime stage --------
FROM python:3.10-slim

WORKDIR /app
ENV PYTHONPATH="/app/src" PYTHONUNBUFFERED=1

# Create non-root user
RUN addgroup --system lux && adduser --system --ingroup lux lux

# Copy dependencies from builder image
COPY --from=builder /install /usr/local

# Copy application code (only runtime files)
COPY --from=builder /app /app

# Install Tesseract OCR for optional PDF image extraction
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

USER lux

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
