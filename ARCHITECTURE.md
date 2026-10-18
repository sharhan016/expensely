# Expense Analyzer Architecture

## Architectural Style

Expense Analyzer is a **modular monolith with selective ports and adapters**. The MVP runs as a Next.js frontend, a FastAPI backend, and PostgreSQL. Statement files remain in private storage. PDF and CSV parsers are replaceable adapters.

```text
Next.js UI
    |
    | JSON/HTTP
    v
FastAPI application
    |-- Identity and settings
    |-- Statement ingestion
    |-- Ledger
    |-- Classification
    |-- People and obligations
    |-- Trips
    |-- Reconciliation and reporting
    v
SQLAlchemy repositories --> PostgreSQL

Adapters: bank parsers | private file storage | optional local OCR
```

Microservices are intentionally excluded from the MVP. They would increase deployment and consistency complexity without benefiting a single-user product.

## System Responsibilities

### Frontend

`frontend/` owns presentation, interaction state, batch-review ergonomics, accessible UI, and theme selection. It must render monetary values returned by the API and must not calculate authoritative balances with JavaScript floating-point arithmetic.

### Backend

`backend/` owns authentication, authorization, financial invariants, decimal calculations, pagination, running balances, aggregates, and atomic workflows. FastAPI route handlers should validate requests and call application services; they should not contain accounting rules.

Target capability boundaries:

```text
app/
  identity/
  ingestion/
    application/
    domain/
    adapters/parsers/banks/
    adapters/storage/
  ledger/
  classification/
  obligations/
  trips/
  reconciliation/
  reporting/
  infrastructure/
```

These boundaries should be introduced incrementally. Avoid abstract repository interfaces for simple CRUD; use ports where replacement or isolation matters, especially parsing, storage, classification, and transaction-scoped workflows.

## Financial Domain

The authoritative accounting record is an immutable bank movement. Its editable financial meaning is stored separately and audited.

Core concepts:

- Account and account type
- Statement, import attempt, and staged import row
- Bank movement
- Transaction meaning
- Merchant and category
- Classification rule and matching evidence
- Person, entrusted-fund entry, and loan entry
- Employer, business expense, and reimbursement allocation
- Trip and transaction membership
- Transfer link
- Reconciliation

Purpose, category, merchant, and responsible person or employer are separate concepts. For example, `Business expense` is a purpose, `Software` is a category, and `OpenAI` is a merchant.

### Invariants

- Use `Decimal` and PostgreSQL `NUMERIC` for money.
- Imported date, amount, description, reference, and bank balance are immutable after confirmation.
- Every record and association belongs to the authenticated user.
- Import confirmation is atomic and duplicate identity is scoped to user and account.
- Credit-card liabilities never contribute to bank cash or available funds.
- Internal transfers link two movements and are neutral in consolidated totals.
- Business expenses require an employer.
- Loans and entrusted funds require a person.
- Reimbursements settle expenses; they are not income.
- Trips reference existing transactions and never copy them.
- Relevant changes inside a reconciled period invalidate its reconciliation.

## Statement Import

The ingestion pipeline is deterministic and does not write ledger records until confirmation:

1. Validate size, extension, MIME signature, and ownership.
2. Store the source privately under an opaque key.
3. Select a parser from document evidence.
4. Produce a canonical parsed-statement result.
5. Detect institution, account identity and type, period, and balances.
6. Stage extracted rows.
7. Detect duplicates and generate classification suggestions.
8. Present evidence and uncertainty for review.
9. Confirm selected rows in one database transaction.
10. Create or bind the account and reconciliation evidence.
11. Apply the configured source-file retention policy.

Bank parsers live under `backend/app/parsers/banks/<bank>/`, return canonical records, and have no database side effects. PDF passwords remain request-scoped and are never stored or logged.

## Classification

Classification follows an ordered deterministic pipeline:

1. Exact user rule.
2. Normalized merchant or recipient identifier.
3. Exact normalized confirmed history.
4. Similar confirmed historical description.
5. Conservative built-in merchant/category mapping.
6. Unresolved review item.

Amount may constrain a match but can never be the only evidence. Each suggestion records its rule, evidence, version, and confidence. Suggestions remain non-authoritative until confirmed or accepted under an explicit high-confidence policy. A future investigative agent may explain unresolved items but cannot commit financial records.

## Obligations and Grouping

- Entrusted funds form a person-specific sub-ledger: receipts increase the person’s balance and instructed payments reduce it.
- Loans form a person-specific principal ledger: money given increases the receivable and repayment reduces it.
- Reimbursements use allocations so one reimbursement can settle several expenses and partial settlements remain traceable.
- Trips use a date range to suggest existing transactions, then persist explicit inclusion or exclusion decisions.
- Transfers use a link between source and destination movements.

Dashboard figures and every detail view must be derived from shared backend reporting services so summary and drill-down values cannot disagree.

## Reconciliation

A reconciliation belongs to one account and one inclusive statement period.

```text
expected closing = opening balance + confirmed movements
difference       = statement closing - expected closing
```

Persist the opening and closing balances, expected balance, difference, movement-set fingerprint or version, status, invalidation reason, and source statement. Adjacent periods should be checked for closing-to-opening continuity. The system may show possible evidence—unresolved rows, overlaps, exclusions, or suspected duplicates—but must not invent a cause.

## Security and Operations

- Reject development secrets outside development.
- Use secure, expiring session cookies and CSRF protection.
- Rate-limit authentication and enforce authorization server-side.
- Validate ownership for every referenced account, person, employer, trip, and rule.
- Bound PDF size, page count, extraction time, and memory use.
- Never log credentials, account numbers, statement contents, or PDF passwords.
- Never send statements to an external AI service by default.
- Define retention, deletion, backup, and recovery procedures.

The deployment unit remains three services: Next.js, FastAPI, and PostgreSQL, plus a private persistent volume for statement files and a reverse proxy with TLS outside local development.

## Verification Strategy

- Pytest domain tests for signs, balances, obligations, and reconciliation.
- Parser fixtures for each bank and statement layout.
- PostgreSQL integration tests for constraints and atomic workflows.
- API tests for authentication and cross-user isolation.
- Vitest tests for frontend logic and components.
- Playwright tests for registration, import, review, classification, and reconciliation.

## Incremental Migration

1. Stabilize current behavior with authorization and PostgreSQL integration tests.
2. Extract import confirmation into one application service.
3. Split bank parsers behind a common parser interface.
4. Extract classification, reporting, and reconciliation services.
5. Add merchant, category, classification evidence, and rule priority.
6. Add association ownership constraints, reimbursement allocations, and transfer links.
7. Separate immutable bank movements from editable meanings with a reversible migration.
8. Move all aggregates and running-balance calculations to the backend.
9. Complete file lifecycle, security hardening, CI, backup, and recovery.

The primary architectural risk is silent financial misclassification, not traffic scale. Domain distinctions, ownership checks, and reconciliation integrity take priority over adding breadth.

## Architecture Decisions

- **ADR-001:** Use a modular monolith for the MVP.
- **ADR-002:** Keep FastAPI for Python PDF/OCR support.
- **ADR-003:** Treat imported bank movements as immutable and financial meaning as editable and auditable.
- **ADR-004:** Keep parsing, duplicate detection, classification, and reconciliation deterministic and local.
- **ADR-005:** Treat PostgreSQL as the financial source of truth.
- **ADR-006:** Stage, review, and atomically confirm imports.
- **ADR-007:** Separate purpose, category, merchant, and responsible party.
- **ADR-008:** Treat credit-card balances as liabilities, not bank funds.
