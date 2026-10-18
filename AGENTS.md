# Repository Guidelines

## Project Structure & Module Organization

Product code belongs at the repository root:

- `frontend/`: Next.js, TypeScript, Tailwind CSS, UI components, and browser tests.
- `backend/`: FastAPI routes, SQLAlchemy models, Alembic migrations, parsers, and Python tests.
- `storage/`: local statement uploads; always ignored and never committed.
- `reference/expense-analyser/`: ignored upstream reference material. Do not modify it or import it at runtime.
- `docker-compose.yml`: local PostgreSQL and application services.

Keep financial rules in backend services, not React components or route handlers. Bank parsers belong under `backend/app/parsers/banks/<bank>/` and return canonical records without database writes.

## Build, Test, and Development Commands

Commands will be added as the application is scaffolded. Prefer stable root-level commands:

- `make setup`: install frontend and backend dependencies and create local configuration.
- `make dev`: run PostgreSQL, FastAPI, and Next.js for development.
- `make test`: run Python, TypeScript, and browser tests.
- `make lint`: run Python and TypeScript linters and format checks.
- `make build`: produce production frontend and backend artifacts.

Until these targets exist, document equivalents in the relevant service README.

## Coding Style & Naming Conventions

Use four spaces in Python and two in TypeScript, JSON, and CSS. Python modules and functions use `snake_case`; classes use `PascalCase`. React components use `PascalCase.tsx`, hooks use `useSomething.ts`, and route folders use lowercase names.

Format Python with Ruff and TypeScript with Prettier; lint with Ruff and ESLint. Use `Decimal`/database numeric types for money—never floating point. Keep immutable bank movement separate from editable financial meaning.

## Testing Guidelines

Use Pytest for backend calculations and parsers, Vitest for frontend logic, and Playwright for critical workflows. Name Python tests `test_*.py` and TypeScript tests `*.test.ts(x)`. Financial calculations, duplicate rules, authorization boundaries, and reconciliation changes require regression coverage. Fixtures must be synthetic or anonymized.

## Commit & Pull Request Guidelines

Use Conventional Commits, matching the existing history: `feat: add batch import review`, `fix: prevent duplicate statement rows`, or `test: cover partial reimbursement`.

Pull requests must explain the outcome, note schema or configuration changes, list verification, and include screenshots for UI work. Keep unrelated refactors separate and link relevant issues.

## Security & Configuration

Keep secrets in ignored `.env` files and provide safe examples in `.env.example`. Never log statement contents, PDF passwords, account numbers, or authentication credentials. Uploaded statements remain private and must not be sent to external AI services by default.
