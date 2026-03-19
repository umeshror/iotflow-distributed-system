FROM python:3.12-slim AS base

RUN addgroup --system iotflow && adduser --system --ingroup iotflow iotflow

WORKDIR /app

COPY apps/worker/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy shared libraries
COPY libs /app/libs

# Copy service source
COPY apps/worker /app

RUN chown -R iotflow:iotflow /app
USER iotflow

# Set PYTHONPATH to include the root of the app so libs can be imported
ENV PYTHONPATH=/app

# Prometheus metrics on 9101
EXPOSE 9101

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9101/metrics')"

CMD ["python", "main.py"]
