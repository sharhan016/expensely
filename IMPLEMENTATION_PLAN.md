# Expense Analyzer Implementation Plan

**Status:** Proposed implementation baseline  
**Prepared:** 2026-10-18  
**Inputs:** `ARCHITECTURE.md`, `PRD.md`, `DESIGN_AUDIT.md`, repository inspection, and automated baseline checks

## 1. Repository Baseline

### Verified facts

- The application is a modular prototype using Next.js 16/React 19, FastAPI, SQLAlchemy, Alembic, and PostgreSQL (`frontend/package.json:5-30`, `backend/pyproject.toml:1-31`, `docker-compose.yml:1-19`).
- Backend routes are concentrated in `main.py`, `imports.py`, and `finance.py`; domain calculations are partly isolated in `services.py` (`backend/app/main.py:27-198`, `backend/app/imports.py:36-410`, `backend/app/finance.py:32-398`, `backend/app/services.py:17-90`).
- The schema contains users, accounts, staged imports, transactions, people, employers, trips, reconciliation records, and classification rules (`backend/app/models.py:54-227`).
- A confirmed `Transaction` currently combines imported bank evidence and editable financial meaning (`backend/app/models.py:171-200`). There is no separate bank-movement/meaning audit model.
- Generic tabular PDF/CSV parsing and one specialized YES BANK credit-card parser exist (`backend/app/parsers/statement.py:42-175`). OCR does not exist.
- Import staging, account detection/binding, row review, duplicate suggestions, confirmation, and protected-PDF retry exist (`backend/app/imports.py:51-220`, `backend/app/imports.py:241-410`).
- The only bank-specific specialized parser is the tested YES BANK credit-card layout; the generic parser is not a guarantee of support for every detected bank.
- Internal transfer is only a purpose enum. No transfer-link entity or counterpart workflow exists (`backend/app/models.py:25-35`, `backend/app/models.py:171-200`).
- Reimbursements are calculated by subtracting all employer receipts from all employer expenses. The `reimbursement_for_id` self-link is not used by the workflow and cannot represent one payment settling several expenses (`backend/app/finance.py:107-134`, `backend/app/models.py:196-198`).
- Reconciliation uses exact `Decimal` arithmetic but persists only period, balances, and status; expected balance, difference, movement fingerprint, source statement, and invalidation reason are not stored (`backend/app/services.py:84-90`, `backend/app/models.py:203-213`, `backend/app/finance.py:334-398`).
- Transaction retrieval is capped at 500 rows, and the frontend computes running balances using JavaScript `Number` (`backend/app/main.py:114-121`, `frontend/src/lib/ledger.ts:4-13`).
- The design audit is explicitly frontend-only and must not be used to disguise backend/product work as visual work (`DESIGN_AUDIT.md:184-198`).

### Verified quality-gate results

Executed without installing dependencies:

| Check | Result |
|---|---|
| Backend Pytest | **Pass:** 13 tests; 25 `datetime.utcnow()` deprecation warnings |
| Backend Ruff lint | **Pass** |
| Backend Ruff format check | **Fail:** `app/finance.py` and `app/imports.py` require formatting |
| Frontend Vitest | **Pass:** 1 test in one file |
| Frontend ESLint | **Pass** |
| Frontend TypeScript no-emit check | **Pass** |
| Frontend production build | **Pass**, but it may reuse build cache and is not a substitute for browser tests |
| Alembic graph | **One head:** `d53eb029a932` |
| PostgreSQL migration/current-state check | **Not verified:** the configured local database was unavailable from the audit environment |
| Browser tests | **Not run:** standalone Python Playwright script is not wired into `make test` and Playwright is not declared in project dependencies (`frontend/tests/ui_smoke.py:1-80`) |

### Working-tree caveat

The audit occurred while uncommitted design-system changes were present. Deleted CSS modules and modified shared components changed during the inspection. Before implementation, create a stable, reviewed commit representing the approved UI baseline. This plan does not assign ownership of or alter those changes.

### Assumptions

- PostgreSQL 16 remains the production source of truth.
- The first beta is private and single-primary-user, but all authorization boundaries remain multi-user safe.
- INR-only reporting remains a working assumption until the PRD currency question is resolved.
- Imported signed amounts use positive for money entering the account and negative for money leaving it, with credit-card liability signs documented per parser.

## 2. Architecture, PRD, and Design Consistency

### Consistent decisions

- All artifacts favor Next.js + FastAPI + PostgreSQL and local deterministic processing.
- All separate bank cash from credit-card liabilities.
- All require review before import confirmation and prohibit amount-only classification.
- Architecture and PRD agree that trips reference existing transactions rather than copying them.
- Architecture and PRD agree that an optional future agent is advisory and cannot commit financial records.
- The design audit preserves the import-review workflow and the bank − entrusted = available equation.

### Inconsistencies and gaps

1. **Immutable movement versus current schema:** Architecture and PRD require immutable bank movement plus editable/auditable meaning, but both are stored on `Transaction` (`ARCHITECTURE.md:59-91`, `PRD.md:49-65`, `backend/app/models.py:171-200`).
2. **Reconciliation invalidation semantics:** The PRD says relevant edits invalidate reconciliation, but the current API invalidates after every meaning-only transaction edit even though purpose/person/employer/trip do not change the bank movement equation (`backend/app/main.py:124-153`). Product must define whether classification changes invalidate bank reconciliation or only a separate review/finalization status.
3. **Finalized history:** PRD requires finalized/reliable history but explicitly leaves locking/reopen behavior unresolved (`PRD.md:501-519`). The current model stores only a status string and no audit reason (`backend/app/models.py:203-213`).
4. **Shared expense semantics:** The PRD correctly leaves split bills and money advanced for another person unresolved. No schema should be created until that decision is made (`PRD.md:501-519`).
5. **Classification vocabulary:** Architecture/PRD separate purpose, merchant, and category; the current schema has purpose only (`ARCHITECTURE.md:59-77`, `backend/app/models.py:171-200`).
6. **Design timing:** `DESIGN_AUDIT.md` approves a UI-only redesign, but several screens depend on API/schema changes. Implement shell, accessibility, and truthful states independently; delay transfer, reimbursement-allocation, audit-history, and server-paginated ledger UI until their backend slices exist.
7. **README overstatement:** The README describes person-wise loans, reimbursements, reconciliation invalidation, and duplicate review as implemented MVP features, but important ownership, allocation, audit, and integrity behavior remains incomplete (`README.md:40-63`).

### Product decisions required before affected slices

Do not invent these rules:

- Meaning of “shared expense” and split allocation.
- Whether entrusted balances may become negative and what a deficit means.
- Loan interest, fees, write-offs, and overpayments.
- Reimbursement or repayment overpayment behavior.
- Refund-to-original-expense linking.
- Transfer date/amount tolerance and treatment of fees or foreign currency.
- Whether reconciliation finalization locks, reopens, or only invalidates.
- Statement-file retention/deletion period.
- Supported launch banks/layouts and whether OCR is a beta requirement.

## 3. Prioritized Findings

### Critical — fix before dependent implementation proceeds

**C1. Cross-user association integrity is not enforced.** Transaction, staged-row, and rule updates accept person, employer, and trip IDs without proving ownership (`backend/app/main.py:124-151`, `backend/app/imports.py:241-285`, `backend/app/finance.py:280-309`). Foreign keys prove existence, not common ownership (`backend/app/models.py:110-129`, `backend/app/models.py:171-200`, `backend/app/models.py:216-227`). This is both a privacy breach and a financial-integrity defect.

**C2. Imported evidence and editable meaning share one record with no audit trail.** This contradicts the core architecture and prevents reliable correction history (`backend/app/models.py:171-200`). Migration design must precede transfer, allocation, and finalized-history work.

**C3. Several unresolved financial rules affect schema design.** Shared expenses, entrusted deficits, overpayments, refunds, transfers with fees/currency, and finalization behavior are unresolved (`PRD.md:501-519`). Do not build those branches until decisions are recorded.

**C4. The repository baseline is not stable.** Uncommitted UI work changed during the audit, and Ruff formatting currently fails. No schema migration should begin until the current product/design baseline is reviewed and committed.

### High — address before the affected feature ships

**H1. Staging duplicate detection can flag legitimate transactions.** It matches only user, date, amount, and exact description, omitting account and reference (`backend/app/imports.py:104-135`). Final confirmation uses a stronger account/reference fingerprint (`backend/app/services.py:46-53`, `backend/app/imports.py:339-365`), so review and confirmation can disagree.

**H2. Account auto-binding can collide.** Detection selects the first account sharing bank, kind, and last four characters (`backend/app/imports.py:60-69`, `backend/app/imports.py:309-318`). Ambiguous candidates should require user choice rather than silently selecting the oldest ID.

**H3. File and database operations are not one recoverable workflow.** Files are written before the request commit; a later validation/database failure can leave orphaned files, and one invalid file can abort the batch (`backend/app/imports.py:148-198`).

**H4. Import confirmation has a check-then-insert race.** A database uniqueness constraint exists, but concurrent confirmations can raise an unhandled integrity error rather than returning an idempotent skipped result (`backend/app/models.py:171-176`, `backend/app/imports.py:334-407`).

**H5. Reimbursements and loans lack allocation history.** Aggregate subtraction loses which receipt settled which obligation and cannot correctly support partial/multiple settlements or approved overpayment behavior (`backend/app/finance.py:107-134`, `backend/app/services.py:56-81`).

**H6. Reconciliation records are insufficient for finalized history.** Expected/difference snapshots, movement-set version/fingerprint, source statement, invalidation timestamp/reason, and continuity evidence are absent (`backend/app/models.py:203-213`). Period order and overlapping reconciliation validation are also absent (`backend/app/schemas.py:192-197`, `backend/app/finance.py:334-369`).

**H7. Frontend running balances are not authoritative.** JavaScript `Number`, a newest-first reversal, and the 500-row API cap can display wrong balances for large ledgers or decimal edge cases (`frontend/src/lib/ledger.ts:4-13`, `backend/app/main.py:114-121`).

**H8. Session security is development-grade.** The default secret is accepted, cookies lack explicit `Secure` and bounded `max_age`, login has no throttling, and cookie-authenticated mutations lack CSRF protection (`backend/app/config.py:6-13`, `backend/app/main.py:44-71`).

**H9. Parser resource usage is unbounded apart from upload bytes.** PDF pages, extraction time, and decompression/memory are not limited; every page and table is loaded (`backend/app/parsers/statement.py:151-166`).

**H10. Critical API and PostgreSQL behavior is untested.** Current tests cover pure calculations/parsers and two SQLite workflow cases, not API authorization, PostgreSQL constraints, atomic imports, concurrent dedupe, sessions, or reconciliation history (`backend/tests/test_financial_logic.py:8-59`, `backend/tests/test_statement_parsers.py:9-68`, `backend/tests/test_trip_workflow.py:13-152`).

### Medium — important improvement

**M1. Rule ordering is accidental.** Active rules have no explicit priority and the first database result wins (`backend/app/imports.py:91-123`, `backend/app/models.py:216-227`).

**M2. History matching predicts only purpose.** It does not preserve merchant/category/person/employer evidence or a rule version (`backend/app/services.py:23-43`).

**M3. Default employer uniqueness is application-only.** Concurrent writes can create multiple defaults (`backend/app/main.py:187-198`, `backend/app/models.py:142-147`).

**M4. Aggregate logic is duplicated in route handlers.** Dashboard, people, reimbursements, trips, and reconciliation calculations are embedded in `finance.py`, weakening reuse and report/detail consistency (`backend/app/finance.py:35-398`).

**M5. Health checks do not test dependencies.** `/health` returns a static response without checking PostgreSQL or storage (`backend/app/main.py:39-41`).

**M6. Time defaults are deprecated and not explicitly UTC-aware.** Current tests emit 25 warnings from `datetime.utcnow()` defaults (`backend/app/models.py:47-50`).

**M7. Error states can present false financial state.** The dashboard suppresses fetch failure and retains zero totals (`frontend/src/app/page.tsx:13-15`), matching the critical trust finding in `DESIGN_AUDIT.md:32-46`.

### Low — optional or deferrable

**L1.** Extract smaller route modules after behavior is protected; do not perform a broad rewrite merely to match the target folder diagram.

**L2.** Add OCR only after supported text-based bank layouts are reliable and product confirms it is required.

**L3.** Add richer analytics only after all totals share authoritative backend reporting queries.

## 4. Recommended Implementation Phases

Each slice must be independently reviewable, reversible where data changes, and releasable behind existing UI behavior where possible.

### Phase 0 — Freeze decisions and establish trustworthy gates

#### Slice 0.1: Stable baseline and executable quality gate

**Depends on:** None  
**Outcome:** One committed baseline and one repeatable command that proves its health.

Tasks:

- Review and commit the approved UI/design changes separately from backend work.
- Fix formatting only after the baseline commit scope is agreed.
- Wire Pytest, Ruff, ESLint, Vitest, TypeScript/build, migration checks, and Playwright into documented commands.
- Replace the standalone browser script with project-declared Playwright tests using synthetic data.
- Add a PostgreSQL test service/database and migration upgrade/downgrade smoke test.
- Record approved answers to product decisions that block later slices.

Acceptance criteria:

- A clean checkout runs the same root verification command in local development and CI.
- A fresh PostgreSQL database upgrades to head, the application starts, and a downgrade/upgrade rehearsal succeeds.
- Browser tests do not depend on undeclared global packages.
- No claim in README or the plan contradicts an executable check.

Automated tests/checks:

- Existing 13 Pytest tests and one Vitest test.
- Ruff lint/format, ESLint, TypeScript, production build.
- Alembic one-head, upgrade-to-head, downgrade-one/re-upgrade tests.
- One Playwright registration/login/import/navigation smoke test.

Definition of done:

- CI and local commands pass from a clean checkout; failures produce actionable artifacts; no feature behavior changes.

**Status: Complete — 18 October 2026.** The baseline was separated into documentation,
approved interface, and quality-gate commits. From a fresh detached worktree, `make setup`
completed and `make verify` passed Ruff, ESLint, TypeScript, 13 Pytest tests, one Vitest
test, Alembic upgrade/downgrade/re-upgrade, the production build, and one Chromium
end-to-end workflow. CI runs the same command and retains Playwright artifacts on failure.
No new product decisions were inferred; the unresolved rules below remain explicit blockers
for their dependent slices. Baseline warnings: 25 uses of deprecated naive UTC timestamps
and eight reported npm dependency vulnerabilities (one moderate, five high, two critical)
require controlled follow-up rather than an unreviewed forced upgrade.

### Phase 1 — Ownership and immutable-ledger foundation

#### Slice 1.1: Authorization boundary for every association

**Depends on:** Slice 0.1  
**Outcome:** No user can attach another user’s person, employer, trip, account, rule, import, or transaction.

Tasks:

- Centralize owned-entity lookup and purpose/association validation.
- Apply it to transaction updates, import-row updates, bulk updates, rule CRUD, trip decisions, and confirmation.
- Decide whether database-level composite ownership constraints are warranted; prefer constraints where practical.
- Add a partial unique PostgreSQL index for one default employer per user.

Migration/rollback:

- Preflight and repair synthetic/test violations before adding constraints.
- Migration adds constraints/indexes; downgrade removes only those protections, not user data.

Acceptance criteria:

- Every reference is either owned by the authenticated user or rejected without revealing foreign data.
- Purpose-specific required associations are enforced consistently.
- At most one default employer exists per user under concurrent writes.

Tests:

- Unit tests for purpose/association policy.
- PostgreSQL integration tests for constraints and concurrent default-employer writes.
- Parameterized API cross-user tests covering every referenced entity and bulk endpoint.
- Playwright test verifies a normal owned-association workflow still works.

Definition of done:

- All association endpoints pass ownership matrices; database constraints match API policy; no cross-user financial link is possible.

**Status: In progress — 18 October 2026.** A centralized association policy now rejects
foreign person, employer, and trip references; applies the documented required-person and
required-employer rules; and is enforced by transaction edits, import-row edits, atomic bulk
edits, rule writes, and import confirmation. Existing account/import/transaction/trip primary
lookups were verified as owner-scoped. The complete quality gate passes with 26 Pytest tests,
including 13 new ownership-policy/API tests, plus the existing frontend, migration, build, and
Chromium checks. Outstanding: the PostgreSQL default-employer uniqueness migration,
concurrency integration test, and owned-association Playwright path require migration approval.

#### Slice 1.2: Separate immutable movement from editable meaning

**Depends on:** Slice 1.1 and approved finalization/audit semantics  
**Outcome:** Imported evidence cannot be changed through ordinary classification, and every meaning correction is auditable.

Tasks:

- Introduce `bank_movements`, `transaction_meanings`, and meaning-change audit history, or an equivalently explicit model approved by architecture review.
- Backfill existing transactions deterministically while preserving IDs through a compatibility layer.
- Route new imports to movement creation plus initial meaning.
- Limit ordinary update APIs to meaning fields.
- Define which edits invalidate bank reconciliation versus classification/finalization review.

Migration/rollback:

- Use expand/backfill/verify/switch/contract stages.
- Verify counts, sums per account, dedupe hashes, statement links, and associations before switching reads.
- Keep old columns during the compatibility period. Rollback switches reads back; destructive column removal is a later migration.

Acceptance criteria:

- Counts and account-period sums are identical before and after migration.
- Imported fields cannot be mutated by classification APIs.
- Meaning changes record actor, timestamp, before/after values, and reason where required.
- Existing routes continue to return compatible responses during migration.

Tests:

- Pure audit/version tests.
- PostgreSQL migration/backfill verification fixtures.
- API immutability and stale-update tests.
- End-to-end import, classify, correct, and inspect-history flow.

Definition of done:

- Financial equivalence is proven; rollback is rehearsed; no editable path can alter imported evidence.

### Phase 2 — Reliable statement-to-ledger workflow

#### Slice 2.1: Recoverable file lifecycle and import states

**Depends on:** Slice 1.2  
**Outcome:** Each uploaded file succeeds or fails independently without orphaned storage or partial ledger writes.

Tasks:

- Add a private storage adapter with opaque keys, cleanup-on-failure, and owner-checked access.
- Define and implement retry, cancel, delete, and retention transitions.
- Make batch responses retain per-file validation and extraction outcomes.
- Add MIME/signature, page-count, extraction-time, and memory safeguards.

Acceptance criteria:

- Failure of one file does not erase successful staging results for other files.
- Cancelled/failed imports follow the approved retention policy.
- Database failure cleans up newly written files.
- Passwords remain request-only and absent from logs.

Tests:

- Unit tests for state transitions and storage cleanup.
- Integration tests injecting storage, parser, and database failures.
- Security tests for traversal, MIME mismatch, oversized/page-heavy PDFs, timeout, and owner access.
- Playwright batch upload with success, protected, and unsupported files.

Definition of done:

- Every import has a recoverable state and storage outcome; no failure leaves hidden financial or file side effects.

#### Slice 2.2: Account identity, overlap, and duplicate consistency

**Depends on:** Slice 2.1  
**Outcome:** Review evidence and final confirmation use the same account-scoped identity rules.

Tasks:

- Create one canonical duplicate-candidate service shared by staging and confirmation.
- Include account identity and reference evidence; preserve legitimate identical transactions.
- Replace silent last-four auto-binding with unique-match or explicit-choice behavior.
- Persist overlap evidence separately from duplicate decisions.
- Handle uniqueness races as idempotent skips or deterministic conflicts.

Acceptance criteria:

- Exact re-import creates zero duplicate movements.
- Same-date/same-amount rows with distinct references remain separable.
- Ambiguous last-four matches never auto-bind.
- Overlap warns but does not reject nonduplicate rows.
- Concurrent confirmation has deterministic results and no unhandled integrity error.

Tests:

- Table-driven duplicate identity unit tests.
- PostgreSQL concurrent confirmation test.
- API tests for exact re-import, partial overlap, ambiguous account identity, and missing references.
- Playwright overlap review and explicit account selection.

Definition of done:

- Staging and confirmation agree on duplicate evidence; account binding is explainable; re-import is idempotent.

#### Slice 2.3: Bank parser adapters and launch support matrix

**Depends on:** Slice 2.1 and approved launch-bank list  
**Outcome:** Every advertised layout has a side-effect-free adapter and fixture-backed behavior.

Tasks:

- Move bank-specific parsing under `parsers/banks/<bank>/` behind a common interface.
- Preserve the working generic table parser and YES BANK parser during extraction.
- Add canonical extraction evidence, skipped-row warnings, and parser version.
- Document OCR as unsupported unless separately approved.

Acceptance criteria:

- Each advertised bank/layout has anonymized fixtures for period, identity, balances, references, signs, and multiline descriptions.
- Unsupported layouts fail with truthful recovery instructions.
- Parsers perform no database or external-network operations.

Tests:

- Parser fixture matrix and malformed-row/property-style cases.
- Password-protected and textless-PDF cases.
- Contract tests asserting canonical parser output.

Definition of done:

- The support matrix matches tests and documentation; parser output is deterministic and side-effect free.

### Phase 3 — Authoritative ledger and classification

#### Slice 3.1: Server-side ledger pagination and running balances

**Depends on:** Slice 1.2  
**Outcome:** Ledger totals and running balances remain exact beyond 500 rows.

Tasks:

- Add deterministic cursor pagination and server-side filters.
- Calculate running balances with PostgreSQL numeric/`Decimal` using explicit same-day ordering.
- Return formatted decimal strings and balance anchors; remove authoritative frontend `Number` calculations.

Acceptance criteria:

- A ledger with more than 500 movements paginates without gaps/duplicates.
- Running balances are stable across pages and filters and match imported balances where provided.
- Same-day ordering follows the approved rule.

Tests:

- Unit tests for cursor/order rules.
- PostgreSQL tests with >500 rows and decimal edge cases.
- API filter/pagination contract tests.
- Vitest rendering tests and Playwright page/filter navigation.

Definition of done:

- Backend values are authoritative; report/detail totals match; frontend performs formatting only.

#### Slice 3.2: Classification model, evidence, and batch review

**Depends on:** Slices 1.2 and 3.1  
**Outcome:** Purpose, merchant, category, responsible party, and confidence are distinct and reviewable.

Tasks:

- Add merchant/category and classification-evidence/version fields.
- Add rule priority, conflict handling, activation, and association outputs.
- Reuse existing normalized-history matching and rules; do not replace deterministic logic.
- Add bulk preview/commit and optional reprocessing of unresolved items.

Migration/rollback:

- New fields are additive and nullable; backfill only evidence that can be proven.
- Downgrade removes additive structures only before users depend on them; otherwise retain data and disable reads.

Acceptance criteria:

- Amount is never sole evidence.
- Conflicts become review items rather than arbitrary first-match results.
- Confirmed category/merchant/person/employer changes are audited.
- One action can classify a large selected set atomically.

Tests:

- Unit tests for ordered matcher/conflicts and normalization.
- Integration tests for rules across accounts and bulk atomicity.
- API tests for evidence/version and reprocessing.
- Playwright batch classification of 100 synthetic rows.

Definition of done:

- Every suggestion is explainable; confirmed meaning is auditable; unresolved records remain explicit.

### Phase 4 — Financial obligations and transfers

#### Slice 4.1: Internal transfer linking

**Depends on:** Slices 1.2 and approved transfer rules  
**Outcome:** Two owned movements can be linked and neutralized only in consolidated reporting.

Tasks:

- Add a transfer-link entity with one-to-one movement constraints.
- Add deterministic candidate suggestions and explicit confirmation/unlinking.
- Keep both account movements in per-account balances.

Acceptance criteria:

- Opposing owned movements can be linked once.
- Consolidated income/expense excludes principal; account ledgers remain unchanged.
- Unmatched transfers are visibly incomplete.
- Fees/currency behavior follows an approved rule or remains unsupported.

Tests:

- Domain tests for neutrality and sign validation.
- PostgreSQL one-to-one and ownership constraints.
- API link/unlink/conflict tests.
- Playwright candidate-review flow.

Definition of done:

- Transfer neutrality and account balances reconcile to the same source movements.

#### Slice 4.2: Person obligations — entrusted funds and loans

**Depends on:** Slice 1.2 and approved deficit/repayment rules  
**Outcome:** Each person has separate, traceable entrusted and loan sub-ledgers.

Tasks:

- Centralize obligation sign conventions in domain services.
- Replace route-level aggregates with traceable sub-ledger queries.
- Add correction/archive behavior without deleting history.

Acceptance criteria:

- Entrusted receipts/payments and loan principal/repayments remain separate.
- Every balance drills down to contributing movements and allocations.
- Unapproved deficit, interest, and overpayment cases are blocked or unresolved, never guessed.

Tests:

- Table-driven domain tests for zero, partial, full, and approved edge cases.
- PostgreSQL/API ownership and traceability tests.
- Playwright person-detail and correction flow.

Definition of done:

- Person totals equal source sub-ledgers exactly and follow approved edge-case policies.

#### Slice 4.3: Employer expenses and reimbursement allocation

**Depends on:** Slice 1.2 and approved overpayment/refund rules  
**Outcome:** Partial and multi-expense reimbursements are traceable and never counted as income.

Tasks:

- Add reimbursement-allocation records between receipt movements and business expenses.
- Replace aggregate subtraction and retire the insufficient single self-link after compatibility migration.
- Add employer management and safe default changes.

Migration/rollback:

- Introduce allocations additively; infer no historical links that cannot be proven.
- Mark historical unallocated receipts for review.
- Keep legacy field until allocation read/write equivalence is verified.

Acceptance criteria:

- One receipt can settle several expenses and one expense can be settled partially.
- Allocations cannot exceed approved receipt/outstanding rules.
- Receipts do not increase income or duplicate expense.
- Employer totals equal allocation detail.

Tests:

- Domain tests for partial/full/multiple allocation and approved overpayment behavior.
- PostgreSQL constraint/concurrency tests.
- API cross-user and allocation-limit tests.
- Playwright reimbursement allocation and drill-down flow.

Definition of done:

- Outstanding employer receivables are reproducible from origins and allocations with no global-offset ambiguity.

### Phase 5 — Reconciliation and finalized history

#### Slice 5.1: Reconciliation snapshot, continuity, and invalidation

**Depends on:** Slices 1.2, 2.2, 3.1, and approved finalization semantics  
**Outcome:** Each account period has reproducible evidence and an explicit lifecycle.

Tasks:

- Persist expected closing, difference, movement fingerprint/version, source statement, status, finalized timestamp, and invalidation reason.
- Validate period order, uniqueness/approved overlap, and adjacent opening/closing continuity.
- Invalidate only for approved events; separate bank reconciliation from classification review if required.
- Add rerun/reopen history without deleting prior evidence.

Migration/rollback:

- Add nullable snapshot fields, backfill by recomputation, verify, then enforce required fields for new records.
- Downgrade preserves legacy reconciliation rows; do not discard audit history after production use.

Acceptance criteria:

- `opening + confirmed movements = expected closing` uses exact decimal arithmetic.
- Difference is reproducible from stored evidence.
- A relevant change records when and why the period became invalid.
- Adjacent discontinuity is flagged without inventing a cause.

Tests:

- Domain tests for signs, decimal precision, and cause-free difference.
- PostgreSQL fingerprint, uniqueness, continuity, and invalidation tests.
- API lifecycle/history tests.
- Playwright balanced, difference, invalidated, and rerun flows.

Definition of done:

- A finalized period can be reproduced and audited; changes never silently rewrite its history.

#### Slice 5.2: Trip workflow hardening

**Depends on:** Slice 3.1  
**Outcome:** Existing date-window behavior is preserved and fully verified.

Tasks:

- Keep the current include/exclude/pending model and one-trip constraint.
- Move totals to shared backend reporting and add edit/archive behavior.
- Add date-boundary and unavailable-transaction evidence.

Acceptance criteria:

- Purchase window contains travel dates.
- Candidate decisions persist and totals include only included source movements.
- A movement cannot be included in two trips.

Tests:

- Retain existing trip workflow regression test.
- Add PostgreSQL ownership/constraint and boundary tests.
- Add Playwright create/review/reopen flow.

Definition of done:

- Trip totals and details match the ledger, with no copied transactions.

### Phase 6 — Security, operations, and approved interface

#### Slice 6.1: Authentication and privacy hardening

**Depends on:** Slice 0.1; can proceed in parallel after ownership policy is stable  
**Outcome:** Production configuration fails closed and private data stays private.

Tasks:

- Reject unsafe production secrets.
- Add secure bounded cookies, CSRF defense, login throttling, and safe error responses.
- Add log-redaction tests and private-file authorization.
- Implement approved export/deletion and retention behavior.

Acceptance criteria:

- Production refuses development secrets and insecure cookie configuration.
- CSRF and brute-force attempts are rejected.
- Logs contain no credentials, PDF passwords, statement contents, or account numbers.
- Export/deletion matches the approved lifecycle.

Tests:

- Auth API tests for register/login/logout/expiry/tampering/throttling.
- CSRF and secure-cookie tests.
- Cross-user file-access and deletion integration tests.
- Log-capture redaction tests.

Definition of done:

- Security checklist and automated suite pass under production-like configuration.

#### Slice 6.2: Ledger Desk interface completion

**Depends on:** Backend contract of each affected slice  
**Outcome:** The approved design is truthful, accessible, responsive, and connected only to working capabilities.

Tasks:

- Implement shell, responsive navigation, semantic landmarks, accessible dialogs, focus handling, and truthful loading/error/empty states first.
- Remove inert controls until backed by working behavior.
- Adapt import switching and transaction tables for mobile without hiding critical actions.
- Implement backend-dependent screens only after their slice APIs stabilize.

Acceptance criteria:

- API failure never looks like a real zero balance or empty ledger.
- Keyboard navigation, focus restoration, 44 px targets, live-region errors, reduced motion, and WCAG 2.1 AA contrast are verified.
- Multi-file import switching works at 390 px.
- Every visible action works or is absent.

Tests:

- Vitest component/state tests.
- Playwright at 390×844, 768×1024, 1024×768, and 1440×900.
- Automated accessibility scan plus manual keyboard smoke documented once per shared component change.
- Visual screenshots for critical states in CI artifacts.

Definition of done:

- All critical workflows meet the approved design audit without changing financial rules in frontend code.

#### Slice 6.3: Operational release gate

**Depends on:** All shipped feature slices  
**Outcome:** The beta can be installed, verified, backed up, restored, and diagnosed.

Tasks:

- Add dependency-aware readiness checks, structured safe logging, backup/restore rehearsal, CI, and release documentation.
- Run a complete synthetic acceptance journey from registration to reconciled import.
- Update README capabilities and limitations to match evidence.

Acceptance criteria:

- Fresh setup, migration, backup, restore, and rollback procedures are exercised with synthetic data.
- CI blocks lint, formatting, migration, unit, integration, E2E, or build failures.
- Known limitations list exactly matches unsupported behavior.

Tests/checks:

- Full automated suite and migration rehearsal.
- Backup/restore checksum and financial-total comparison.
- Production-like smoke test with private storage and PostgreSQL readiness.

Definition of done:

- Release evidence is reproducible without manual testing of every workflow; only exploratory UX acceptance remains manual.

## 5. Dependency Order

```text
0.1 Stable gates
 ├─> 1.1 Ownership boundaries
 │    └─> 1.2 Immutable movement / editable meaning
 │         ├─> 2.1 Import lifecycle ─> 2.2 Identity/dedupe
 │         ├─> 3.1 Authoritative ledger ─> 3.2 Classification
 │         ├─> 4.1 Transfers
 │         ├─> 4.2 Person obligations
 │         └─> 4.3 Reimbursements
 ├─> 2.3 Parser adapters (after 2.1 and support-list decision)
 └─> 6.1 Security hardening

2.2 + 3.1 + 1.2 ─> 5.1 Reconciliation history
3.1 ─> 5.2 Trips hardening
Stable APIs ─> 6.2 Approved interface
All shipped slices ─> 6.3 Release gate
```

## 6. Verification Standard for Every Slice

A slice is not complete when code merely exists. It must satisfy all applicable checks:

1. **Traceability:** Link the PRD acceptance criterion and architecture invariant in the change description.
2. **Domain proof:** Pure unit tests cover signs, decimal boundaries, state transitions, and prohibited cases.
3. **Persistence proof:** PostgreSQL integration tests cover constraints, transactions, concurrency, and migration behavior.
4. **Authorization proof:** Positive owner case plus negative cross-user cases for every ID accepted by an API.
5. **API proof:** Request/response, validation, idempotency, and safe-error tests.
6. **UI proof:** Vitest for component logic; Playwright for consequential multi-step workflows.
7. **Migration proof:** Upgrade on representative pre-migration data, invariant checks, downgrade or compatibility rollback, and re-upgrade.
8. **Operational proof:** No sensitive logs, clear failure state, and relevant health/readiness behavior.
9. **Regression proof:** Existing working behavior remains covered rather than rebuilt without evidence.
10. **Documentation proof:** README/support matrix/known limitations match shipped behavior.

## 7. Global Definition of Done

For every vertical slice:

- Acceptance criteria are executable or directly evidenced.
- Financial calculations use `Decimal`/PostgreSQL numeric values in backend services.
- Imported evidence remains immutable and corrections are auditable.
- All reads, writes, and referenced associations enforce ownership.
- Database writes are atomic and idempotent where retries are possible.
- Schema changes have an expand/verify/switch strategy and a tested rollback path.
- Unit, PostgreSQL integration, API, and appropriate Vitest/Playwright tests pass.
- Loading, empty, success, retry, and error states are truthful and accessible.
- No statement content, password, account number, credential, or private fixture enters logs or version control.
- Root lint, format, test, migration, and build gates pass from a clean checkout.
- Documentation describes only verified functionality.

## 8. Recommended First Slice

Start with **Slice 0.1: Stable baseline and executable quality gate**, immediately followed by **Slice 1.1: Authorization boundary for every association**.

The baseline slice is first because the working tree is changing, the format gate fails, PostgreSQL migrations have not been exercised, and browser coverage is not reproducible. The ownership slice is the first product-code change because current endpoints can create cross-user financial associations. Fixing that boundary before new models or workflows prevents privacy violations and stops invalid data from contaminating later migrations.
