# Expense Analyzer

A private, statement-first financial workspace for managing multiple bank accounts,
entrusted funds, loans, company reimbursements, trips, and reconciliation.

## Architecture

- Next.js and TypeScript frontend
- FastAPI backend
- PostgreSQL
- SQLAlchemy and Alembic migrations
- Local-first PDF extraction with optional OCR

The application is developed independently. The repository under
`reference/expense-analyser/` is ignored and used only to study statement-format
edge cases and test expectations.

## Local setup

```bash
cp .env.example .env
docker compose up -d db
cd backend && uv sync && uv run alembic upgrade head
```

Run the API and web app in separate terminals:

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev
```

Open `http://localhost:3000`, create the private beta account, and import one or
more statements. PDF passwords are used only for the active extraction request
and are never persisted.

## Implemented MVP

- Session authentication and private, size-limited statement storage.
- Multi-file PDF/CSV import, protected-PDF retry, staged review, duplicate flags,
  bulk classification, and confirmation into the ledger.
- Unified multi-account ledger, people and entrusted-fund balances, person-wise
  loans, default-employer reimbursements, trips, reusable rules, and period-based
  reconciliation with invalidation after ledger edits.
- Six user-selectable application color themes.

## Verification

```bash
cd backend && uv run ruff check app tests && uv run pytest -q
cd frontend && npm run lint && npm run build
```

## Current limitations

PDF extraction currently covers delimited exports, table-based PDFs, and the
tested YES BANK credit-card layout. More bank-specific parsers are still needed.
Scanned-image OCR and screenshot imports are not yet implemented; the app does
not claim that scanned statements are readable. Matching rules require a
description signal and never match solely by amount.
