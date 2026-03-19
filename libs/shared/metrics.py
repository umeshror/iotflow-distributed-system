from prometheus_client import start_http_server

def start_shared_metrics_server(port: int):
    """Start Prometheus HTTP metrics server on configured port."""
    start_http_server(port)
