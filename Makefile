.PHONY: install test lint check run mcp docker-up docker-down

install:
	python -m pip install -e '.[dev]'

test:
	pytest -q

lint:
	ruff check src tests

check:
	python -m pytest
	python -m ruff check .
	python scripts/check_long_lines.py

run:
	uvicorn business_graph_api.main:app --reload --host 0.0.0.0 --port 8000

mcp:
	python -m business_graph_mcp.server

docker-up:
	docker compose up -d

docker-down:
	docker compose down
