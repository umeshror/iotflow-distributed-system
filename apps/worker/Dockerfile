# --- Build Stage ---
FROM python:3.12-slim AS builder

WORKDIR /build

COPY apps/worker/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Final Stage ---
FROM python:3.12-slim

RUN groupadd -r iotflow && useradd -r -g iotflow iotflow

WORKDIR /app

COPY --from=builder /install /usr/local

# Copy shared libraries and service source
COPY libs /app/libs
COPY apps/worker /app/worker

RUN chown -R iotflow:iotflow /app
USER iotflow

ENV PYTHONPATH=/app/worker:/app

EXPOSE 9101

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9101/metrics')"

WORKDIR /app/worker
CMD ["python", "main.py"]
