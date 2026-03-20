# IoTFlow Production Makefile

.PHONY: help up down build ps logs test lint format simulate clean

# Default target
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Infrastructure:"
	@echo "  up             Start all services in detached mode"
	@echo "  down           Stop all services and remove containers"
	@echo "  build          Build or rebuild services"
	@echo "  ps             List running services"
	@echo "  logs           View logs for all services"
	@echo ""
	@echo "Development:"
	@echo "  test           Run all tests with coverage"
	@echo "  lint           Run mypy, black, and isort checks"
	@echo "  format         Auto-format code using black and isort"
	@echo "  simulate       Run the high-fidelity IoT device simulator"
	@echo "  clean          Remove temporary files and __pycache__"

# --- Infrastructure ---

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

ps:
	docker compose ps

logs:
	docker compose logs -f

# --- Development ---

test:
	pytest --cov=apps --cov=libs tests/

lint:
	black --check .
	isort --check-only .
	mypy .

format:
	black .
	isort .

simulate:
	python3 scripts/simulate_iot.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .coverage
