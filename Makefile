.PHONY: test-pg setup dev dev-backend dev-frontend db-up db-down migrate seed test integration lint \
	typecheck build test-db-reset test-db-down migration-smoke e2e verify

TEST_DATABASE_URL ?= postgresql+psycopg://expense_test:expense_test@127.0.0.1:55435/expense_analyzer_test

setup:
	cd backend && uv sync
	cd frontend && npm install
	cd frontend && npx playwright install chromium
	cp -n .env.example .env || true

db-up:
	sh scripts/compose.sh up -d --wait db

db-down:
	sh scripts/compose.sh down

migrate:
	cd backend && uv run alembic upgrade head

seed:
	cd backend && uv run python scripts/seed_demo.py --email "$${DEMO_EMAIL}"

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev:
	@trap 'kill 0' EXIT INT TERM; $(MAKE) dev-backend & $(MAKE) dev-frontend & wait

test:
	cd backend && .venv/bin/pytest
	cd frontend && npm test -- --run

integration:
	cd backend && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" .venv/bin/pytest tests/integration

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run lint

typecheck:
	cd frontend && ./node_modules/.bin/tsc --noEmit --incremental false

build:
	cd frontend && npm run build

test-db-reset:
	-sh scripts/compose.sh --profile test rm -sf test-db
	sh scripts/compose.sh --profile test up -d --wait test-db

test-db-down:
	-sh scripts/compose.sh --profile test rm -sf test-db

UNIT_DATABASE_URL ?= postgresql+psycopg://expense_test:expense_test@127.0.0.1:55435/expense_unit

# Runs the workspace-based backend tests on PostgreSQL (needs test-db-reset first).
test-pg:
	docker exec expense-tracker-test-db-1 psql -U expense_test -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='expense_unit'" | grep -q 1 || docker exec expense-tracker-test-db-1 psql -U expense_test -d postgres -c "CREATE DATABASE expense_unit"
	cd backend && UNIT_DATABASE_URL="$(UNIT_DATABASE_URL)" .venv/bin/pytest tests --ignore=tests/integration

migration-smoke:
	cd backend && DATABASE_URL="$(TEST_DATABASE_URL)" .venv/bin/alembic upgrade head
	cd backend && DATABASE_URL="$(TEST_DATABASE_URL)" .venv/bin/alembic downgrade -1
	cd backend && DATABASE_URL="$(TEST_DATABASE_URL)" .venv/bin/alembic upgrade head

e2e:
	cd frontend && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" npm run test:e2e

verify: test-db-reset lint typecheck test test-pg migration-smoke integration build e2e
