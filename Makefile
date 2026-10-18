.PHONY: setup dev dev-backend dev-frontend db-up db-down migrate test lint build

setup:
	cd backend && uv sync
	cd frontend && npm install
	cp -n .env.example .env || true

db-up:
	docker compose up -d db

db-down:
	docker compose down

migrate:
	cd backend && uv run alembic upgrade head

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev:
	@trap 'kill 0' EXIT INT TERM; $(MAKE) dev-backend & $(MAKE) dev-frontend & wait

test:
	cd backend && uv run pytest
	cd frontend && npm test -- --run

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

build:
	cd frontend && npm run build
