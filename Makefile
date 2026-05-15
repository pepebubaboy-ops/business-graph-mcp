.PHONY: install test lint run mcp docker-up docker-down

install:
	python -m pip install -e '.[dev]'

test:
	pytest -q

lint:
	ruff check src tests

run:
	uvicorn business_graph_api.main:app --reload --host 0.0.0.0 --port 8000

mcp:
	python -m business_graph_mcp.server

docker-up:
	docker compose up -d

docker-down:
	docker compose down
