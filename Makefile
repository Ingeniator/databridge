.PHONY: dev dev-worker dev-up dev-down test test-unit test-integration test-e2e migrate lint

dev:
	uv run uvicorn databridge.main:app --host 0.0.0.0 --port 5010 --reload

dev-worker:
	uv run arq databridge.export.worker.WorkerSettings

dev-up:
	docker compose -f docker-compose.dev.yml up -d
	@echo "Waiting for postgres..."
	@docker compose -f docker-compose.dev.yml exec postgres sh -c 'until pg_isready -U postgres; do sleep 1; done'
	@[ -f config.yaml ] || cp config.dev.yaml config.yaml
	$(MAKE) migrate

dev-down:
	docker compose -f docker-compose.dev.yml down

test:
	uv run pytest tests/ -v

test-unit:
	uv run pytest tests/unit/ -v

test-integration:
	uv run pytest tests/integration/ -v

test-e2e:
	uv run pytest tests/e2e/ -v

migrate:
	uv run alembic upgrade head

lint:
	uv run ruff check src/ tests/
