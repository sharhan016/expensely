# Expense Analyzer — Agent Implementation Progress

Last updated: 2026-10-22  
Branch: `agentic-development`  
Source specification: `expense-analyzer-agent-first-workflow-prompt.md`
Current plan: `docs/alignment-and-plan.md` (Increments 1a and 1b done; Increment 2 awaits approval)

## How to use this file

Read this file before continuing agent-first work. Treat the source specification as the product authority and this file as the implementation handoff. Verify the working tree and recent commits before relying on any status below.

Update this file after each completed slice with:

- the outcome and important design decisions;
- feature and test commit hashes;
- verification performed;
- newly discovered gaps or changed priorities.

This is the committed copy of the handoff (the original was git-ignored and local only). Never put financial data, credentials, account numbers, statement contents, or other secrets here. The product evaluation and increment plan are in `docs/alignment-and-plan.md`.

Status legend: **Done** = implemented and verified; **Partial** = useful behavior exists but acceptance is incomplete; **Not started** = no milestone-specific implementation.

## Increment 1a: statements I can trust (done 2026-10-22)

Each fix has a regression test; failing-before status is in the commit report, not here.

- Duplicate handling is account-scoped and reference-aware. Identical rows in one statement get a deterministic ordinal (first occurrence keeps its original hash). Read-only `GET /imports/possibly-missed-rows` lists confirmed statements whose rows are missing from the ledger.
- Confirm reconciles a statement against its own movements; the whole-period difference is reported separately (`statement_difference`, `period_difference`).
- Balances carry the newest confirmed statement date, an age and a stale flag (`app/balances.py`). Dashboard and account money is returned as exact decimal strings.
- Running balances come from the backend as Decimal; the frontend only formats. `GET /transactions` returns `{items,total,showing,limit,offset}` with date/account filters.
- Suggestion history reads only confirmed decisions (`confirmed_rule`, `user_confirmed`). Review-time and later purpose edits are recorded as `user_confirmed`; automatic links as `automatic_rule`.
- Shared `privacy_probe` fixture (`backend/tests/conftest.py`) plus failure-path tests. Unhandled server errors are contained and logged by class and route only.

Known gaps found: a corrupt PDF is classified `password_required`; legacy `legacy_migration` meanings are not treated as confirmed history; no `statement fingerprint` yet; the possibly-missed report is advisory only.

## Increment 1b: what my records cover (done 2026-10-22)

- `app/coverage.py` (pure): per-account chain from `records_start` through consecutive balanced, verifiable, un-invalidated statements whose balances connect. States: covered, bridged (date gap with exactly matching balances; shown separately, can be un-bridged), gap, discontinuity, difference, unverifiable, unverified, invalidated. Overall = earliest in-scope account, naming the laggard.
- `app/coverage_service.py`: spans from confirmed statements and `Reconciliation` evidence; movement fingerprint; idempotent recompute (`POST /coverage/recompute`); `GET /coverage`; `PATCH /coverage/accounts/{id}` (in_scope, records_start, optional confirmed opening balance); un-bridge / re-bridge endpoints.
- `app/coverage_hooks.py`: a flush hook invalidates covering reconciliations when a `Transaction` is added, removed, or its amount, date or account changes. Meaning edits do not. Sessions loading data against an older schema opt out with `session.info["skip_coverage_invalidation"]`.
- Migration `a7d3c5e1f904`: account `in_scope`, `records_start`, `records_opening_balance`; reconciliation `source_statement_id`, `movement_fingerprint`, `computed_difference`, `invalidated_at`, `invalidation_reason`; tables `coverage_gap_unbridges`, `cash_withdrawal_markers`, `cash_statement_entries`. Existing reconciliations are unverified until "Re-check records". Existing credit-card accounts start out of scope (card statements have no closing balance, so they cannot be reconciled yet).
- Cash bucket (`app/cash.py`, `/cash`): narration rule marks ATM/cash-withdrawal debits on bank accounts only; reversible markers (`source` rule or user; an unmarked withdrawal is never re-marked); her own cash entries are labeled "No statement behind this"; reported only as "withdrawn, not yet explained", never a cash balance; never counts toward reconciled-through. `neutral_movement_ids()` lists movements that future income/spending totals must exclude (no such totals exist yet).
- UI: Records page, coverage banner on Home and the assistant, cash markers in the ledger.
- Housekeeping: workspace-fixture tests run on PostgreSQL (`make test-pg`, in `make verify`); balance-aware handling of same-day identical rows split across statements; corrupt PDFs are no longer reported as password problems; assistant notes legacy entries not used for suggestions.

Known limits: only tests using the shared `workspace` fixture run on PostgreSQL (older test modules build their own SQLite engines); recompute judges a statement on its own rows, so a movement added from elsewhere invalidates the period but a re-check can restore it; card reconciliation is not built; the manual `POST /reconciliations` still writes rows without a fingerprint (they read as unverified).

## Current outcome

The application now supports a meaningful first vertical slice:

1. A user can open the private assistant and upload supported YES BANK current-account or credit-card statements.
2. The system identifies the instrument from document evidence, stages immutable rows, reconciles when balance endpoints exist, and blocks silent posting of unexplained differences.
3. Proven re-imports are skipped without deleting or merging genuine movements.
4. Repeated UPI counterparties and conservative card-merchant tokens become durable identities and analytical groups while every ledger row remains separate.
5. Credit-card purchases, fees, interest, taxes, EMI-related entries, cash advances, payments, refunds, reversals, and foreign-currency adjustments have separate transaction semantics.
6. Exact card-payment pairs and unique refund/reversal pairs are linked without double-counting. Unmatched payments remain visible.
7. Confirmed counterparty meaning can be reused for future rows. Retrospective changes are explicit proposals that can be accepted, rejected, and safely reverted.

The milestone is not fully complete. The largest remaining gaps are resumable workflow orchestration, allocation-aware partial card payments, ambiguous account selection, broader obligation memory, and formal privacy/logging regression coverage.

## Delivery-slice status

| Slice | Status | Implemented | Still needed |
| --- | --- | --- | --- |
| 1. Trustworthy YES BANK intake | **Done for supported layouts** | Evidence-based current/savings/card detection; masked identity; staging; reconciliation evidence; account binding; dedupe; explicit unsupported/unknown handling; distinct card statement amounts | Add more real, anonymized YES layout fixtures as they become available; improve running-balance diagnostics |
| 2. Counterparty understanding | **Done for UPI and conservative card merchants** | Durable counterparties and aliases; repeated-flow totals; totals split by purpose; immutable source rows; confirmed future defaults; merchant tokens ignore changing references | Confirm/merge multiple aliases into one identity; add conservative NEFT/IMPS/merchant-acquirer identifiers; improve ambiguous-name review |
| 3. Credit-card payment linking | **Partial** | Multi-field exact payment matching; one completed link per movement; retained source rows; consolidated double-count prevention; unique refund/reversal linking; rejection/undo; unmatched list | Allocation-aware partial payments and overpayments; stronger reference/card-identity scoring; manual link proposal/approval flow |
| 4. Conversational operator | **Partial** | Private persisted assistant conversation; conversational uploads and summaries; focused upload review; action proposal audit states | Durable per-stage progress events; resumable pause/retry state; explicit tool/action permissions; complete audit coverage for automatic actions; richer blocking questions |
| 5. Broader financial memory | **Partial foundation** | Existing people, employers, loans, entrusted funds, reimbursements and trips; immutable meaning history; reversible retrospective counterparty proposals | Typed loan/reimbursement/entrusted/repayment links; settlement and outstanding-balance explanations; trip-aware learned flows; approval-gated obligation automation |

## Completed implementation and commits

### Agent-first workflow foundation

- `e916b19 feat: add agent-first statement workflow`
- `6db7c39 test: cover agent-first financial workflows`
- Added private assistant messages and upload flow, YES current/card identification, counterparty aliases and flows, card-payment matching, reconciliation gating, and browser coverage.

### Credit-card transaction semantics

- `ecee948 feat: add credit-card transaction semantics`
- `d4d6f68 test: cover card semantics and refund links`
- Separated card category from financial purpose.
- Added structured category evidence, confidence, and decision source.
- Added conservative unique refund/reversal matching and reversible rejection.

### Analytical transaction groups

- `d243685 feat: persist analytical transaction groups`
- `1ad8c17 test: prove transaction group preservation`
- Added first-class groups and memberships by counterparty, purpose, and direction.
- Meaning corrections move memberships without changing movement count or amount.

### Reversible retrospective learning

- `96f7f44 feat: add reversible retrospective proposals`
- `c5cdcd3 test: cover retrospective proposal decisions`
- Added proposed, accepted, rejected, and reverted agent actions.
- Past unresolved rows do not change until approval; unsafe undo is blocked after later edits.

### Correct credit-card statement amounts

- `19208d3 feat: separate credit-card statement amounts`
- `ca48e66 test: cover card statement amount semantics`
- Stored statement balance, total amount due, minimum amount due, and available credit separately.
- Migration moves the legacy card closing value to total due and restores it on rollback.

### Constrained account instruments

- `66e0396 feat: constrain account instrument types`
- `bf52884 test: prove account instrument constraints`
- Added the `InstrumentType` enum for bank/current/savings/credit-card accounts.
- PostgreSQL rejects arbitrary instrument values; migration and rollback preserve existing valid values.

### Recurring card merchants

- `c92fedd feat: recognize recurring card merchants`
- `6d245c3 test: cover recurring card merchant flows`
- Added conservative merchant tokens for purchases/refunds/reversals.
- Changing channel labels and reference numbers do not split identical merchants.
- Card payments and unrelated card activity are not treated as merchants.

## Key design invariants already enforced

- Authoritative money uses `Decimal`/numeric types, never floating point.
- Imported movements remain immutable and source-traceable.
- Duplicate, related movement, and repeated flow are separate concepts.
- Analytical groups reference movements; they never replace or merge them.
- Counterparty identity, purpose, category, responsibility, and relationship links remain distinct.
- Credit cards are liabilities and are excluded from bank-cash totals.
- Linked card payments remain visible in both ledgers and contribute zero consolidated income/expense.
- Obligation-changing defaults are not automatically learned; safe counterparty defaults are restricted to personal, income, and refund.
- Raw statements are processed locally; no external LLM is required or called.

## Highest-priority remaining work

### P0 — Resumable workflow state and progress

Implement durable workflow runs/events for receive, inspect, extract, validate, bind, dedupe, resolve entities, connect flows, apply knowledge, post, respond, and learn.

Acceptance:

- Each stage has a user-friendly persisted event and machine-readable status.
- A workflow can pause for one focused question and resume without repeating completed side effects.
- Recoverable failures can retry safely and do not duplicate ledger rows, links, aliases, or actions.
- Assistant history can explain the current stage and blocker after a restart.

### P0 — Partial and allocated credit-card payments

Extend transaction links with allocation-aware relationships.

Acceptance:

- One bank payment may settle part of one card payment or be allocated across supported settlement entries.
- Partial payments and overpayments are visible and never auto-marked as exact matches.
- Allocated totals cannot exceed movement amounts.
- Reversing/rejecting an allocation restores unmatched balances without changing source rows.

### P1 — Ambiguous account binding

Current high-confidence identity can bind automatically, and incompatible account types are rejected. The workflow still needs a first-class ambiguity result when multiple owned accounts are plausible.

Acceptance:

- Bank name or filename alone never selects an account.
- Two plausible accounts produce one focused question listing safe masked candidates.
- The confirmed binding is remembered with evidence and provenance.

### P1 — Counterparty alias confirmation and merging

Add an owner-scoped action for attaching a new alias to an existing counterparty and safely merging accidentally duplicated detected identities.

Acceptance:

- The UI previews affected movements and totals.
- Alias uniqueness remains owner-scoped.
- Raw descriptions and ledger identities do not change.
- Merge/undo is audited and cannot cross users.

### P1 — Broader obligation links

Add typed links and allocations for loan disbursements/repayments, entrusted money, reimbursable expenses/repayments, and shared expenses.

Acceptance:

- No automatic suggestion changes an obligation balance without approval and supporting evidence.
- Outstanding and settled totals drill down to source movements.
- The same person can have separate personal, loan, entrusted, and reimbursement subtotals.

### P1 — Complete conversational summary

Expand the assistant response to include balance-valid-as-of context, new/skipped/unresolved counts, spending/incoming totals by meaning, repeated flows, links/unmatched payments, and affected obligations.

Never say “spent” for transfers, loans, entrusted payments, reimbursements, or unresolved rows.

### P1 — Privacy and diagnostics regression suite

Add tests that capture application logs and failure diagnostics during uploads, password failures, parser failures, and reconciliation errors.

Acceptance:

- No transaction descriptions, names, amounts, account/card numbers, PDF passwords, or statement bodies appear in logs.
- Optional future LLM integration receives only explicit minimum redacted context and is disabled by default.

### P2 — Additional parser coverage

Add evidence-backed layouts only from synthetic or anonymized fixtures. Keep parsers under `backend/app/parsers/banks/<bank>/` and return canonical records without database writes.

## First-milestone definition-of-done check

| Requirement | Status | Note |
| --- | --- | --- |
| Detect supported YES current/card statement without preselection | **Done** | Uses document evidence and masked identity |
| Reconciled conversational explanation | **Partial** | Works when layout supplies balance endpoints; response needs fuller balance-valid context |
| Preserve every genuine movement | **Done** | Groups and links reference immutable rows |
| Skip proven duplicates | **Done** | Re-import/overlap coverage exists |
| Group repeated counterparties without conflating meanings | **Done for supported identifiers** | UPI and conservative card merchant identifiers covered |
| Link card payment when both exact sides exist | **Done** | Partial/allocation-aware linking remains |
| Avoid consolidated double-counting | **Done** | Covered in backend and browser tests |
| Ask only for decisions that cannot be made safely | **Partial** | Review gates exist; resumable focused-question orchestration remains |

## Important code map

- `backend/app/parsers/banks/yes/statement.py` — YES deposit/card detection and parsing.
- `backend/app/card_semantics.py` — card transaction category rules and evidence.
- `backend/app/imports.py` — staging, reconciliation, dedupe, account binding, posting, and entity resolution.
- `backend/app/counterparty_service.py` — stable UPI and card-merchant identity extraction.
- `backend/app/transfer_matching.py` — card-payment and refund/reversal matching.
- `backend/app/grouping.py` — analytical group synchronization.
- `backend/app/assistant_routes.py` — persisted assistant conversation and deterministic summaries.
- `backend/app/action_routes.py` — proposal decisions and reversible retrospective actions.
- `frontend/src/app/assistant/page.tsx` — conversation-first entry point.
- `frontend/src/app/imports/page.tsx` — statement/account review and posting.
- `frontend/src/app/flows/page.tsx` — counterparties, grouped flows, links, and proposals.
- `frontend/tests/e2e/core-workflow.spec.ts` — critical end-to-end acceptance workflows.

## Last verified state

- Backend after 1a: see the 1a report; earlier baseline was `70 passed, 5 skipped`.
- PostgreSQL integration/migrations: `5 passed`.
- Frontend: TypeScript, Vitest, ESLint, and production build passed.
- Playwright: full suite `5 passed`; focused card/merchant workflow passed after the last merchant change.
- Disposable PostgreSQL test database was removed after verification.
- Expected Git working tree before this tracker request: clean.

## Verification commands

Run proportionally to the changed slice:

```sh
backend/.venv/bin/ruff check backend/app backend/tests backend/migrations/versions
backend/.venv/bin/ruff format --check backend/app backend/tests backend/migrations/versions
backend/.venv/bin/pytest backend/tests -q

cd frontend
./node_modules/.bin/tsc --noEmit --incremental false
npm test -- --run
npm run lint
npm run build

cd ..
make test-db-reset migration-smoke integration
cd frontend
TEST_DATABASE_URL="postgresql+psycopg://expense_test:expense_test@127.0.0.1:55435/expense_analyzer_test" npx playwright test
cd ..
make test-db-down
```

Restore `frontend/next-env.d.ts` if Playwright rewrites its generated type-import paths before committing.

## Suggested next implementation slice

Per `docs/alignment-and-plan.md`: Increment 1b (coverage and reconciled-through) after approval. The text below is the earlier, superseded suggestion.


Start with **P0 — Resumable workflow state and progress**. Keep it narrow:

1. Add a reversible migration for workflow runs and append-only progress events.
2. Record deterministic events around the existing upload/stage/confirm services; do not move financial logic into the assistant route.
3. Expose owner-scoped run/event responses through the assistant API.
4. Show current progress and a recoverable blocker in the assistant UI.
5. Add unit, migration-preservation, authorization, and one browser resume test.
6. Commit as a feature commit followed by a matching test commit.
