FROM python:3.12-slim AS base

# Security: run as non-root user
RUN addgroup --system iotflow && adduser --system --ingroup iotflow iotflow

WORKDIR /app

# Install dependencies in a separate layer
COPY services/ingestion/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy shared models
COPY services/shared /app/services/shared

# Copy service source
COPY services/ingestion /app

RUN chown -R iotflow:iotflow /app
USER iotflow

# Prometheus metrics on 9100, FastAPI on 8000
EXPOSE 8000 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
