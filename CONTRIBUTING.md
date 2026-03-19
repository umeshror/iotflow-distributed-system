# Contributing to IoTFlow

Thank you for your interest in contributing to IoTFlow! We welcome contributions from the community to help make this system more robust and scalable.

## How to Contribute

1.  **Report Bugs**: Open an issue if you find a bug or something that doesn't work as expected.
2.  **Suggest Features**: Have an idea to make IoTFlow better? Open a feature request!
3.  **Submit Pull Requests**:
    *   Fork the repository.
    *   Create a new branch for your changes.
    *   Ensure your code follows the existing style and is well-documented.
    *   Add tests for any new functionality.
    *   Open a PR with a clear description of your changes.

## Coding Standards

To maintain a high level of code quality, we follow these standards:

- **Python**: Use Python 3.12+ features. Follow PEP 8.
- **Asyncio**: Use `async`/`await` for all I/O bound operations. Avoid blocking calls in the event loop.
- **Typing**: Use static type hints everywhere (PEP 484).
- **Validation**: Use Pydantic for all data validation and configuration.
- **Logging**: Use `structlog` for structured, JSON-ready logging.
- **Metrics**: Instrument new features with Prometheus metrics where appropriate.
- **Tests**: Add unit tests for core logic in `libs/shared` and integration tests for service components in `tests/`.

## Development Setup

To run IoTFlow locally for development:

```bash
# 1. Clone your fork
git clone https://github.com/YOUR_USERNAME/Iot-flow.git
cd Iot-flow

# 2. Start the infrastructure
docker compose up -d --build

# 3. Simulate realistic events
python3 scripts/simulate_iot.py
```

## Community

*   Join our discussions in the GitHub Issues.
*   Follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing to IoTFlow, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
