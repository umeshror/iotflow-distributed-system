# --- Build Stage ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Separate layers for performance and security
COPY apps/ingestion/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Final Stage ---
FROM python:3.12-slim

# Use non-root user for security
RUN groupadd -r iotflow && useradd -r -g iotflow iotflow

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install /usr/local

# Copy shared libraries and service source
COPY libs /app/libs
COPY apps/ingestion /app/ingestion

# Set ownership to non-root user
RUN chown -R iotflow:iotflow /app
USER iotflow

# Set PYTHONPATH to include the root of the app so libs can be imported
ENV PYTHONPATH=/app/ingestion:/app

EXPOSE 8000 9100

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

WORKDIR /app/ingestion
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
