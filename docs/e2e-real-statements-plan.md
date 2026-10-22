# End-to-end plan: real statements through the test account

Local only. The statements and passphrase live in `reference-docs/` (git-ignored). Nothing from
them is written to the repository, logs or this document; run output keeps counts and redacted
text only.

## What is checked

1. **Every calculation, against an independent oracle.** The oracle reads each PDF's own balance
   column (never our parsers) and the test compares: parsed rows (date, signed amount, balance),
   opening/closing and reconciliation, ledger running balances, account balance and as-of date,
   report totals per bucket and per day, drill-down sums, CSV/TSV exports, dashboard parity,
   coverage, idempotent re-upload, investigation matches. (`backend/tests/test_real_statements_local.py`,
   also run on PostgreSQL.)
2. **Recency.** Statements ending within days are current; weeks old are flagged "not today's
   balance"; months old are stated as old. The wording and the 35-day threshold are reviewed.
3. **Classification sense.** Rails (UPI, IMPS, NEFT, ATM, charges), counterparty detection
   coverage, cash markers, which rows are asked about, how many questions she faces, and whether
   obvious bank charges are left "unexplained".
4. **Conversation and UX in the browser** (test account, desktop and 390px): first visit asks
   the period; protected PDFs ask for a password once each; progress and limits are stated;
   review and confirm; returning greeting; questions grouped by person; "what is still out",
   "where did my N go", date-range reports, cash and coverage questions; report, Records and Home.
5. **Failure behaviour.** Wrong passphrase, same file twice, a statement that does not reconcile.

## Pass criteria
- Every figure equals the oracle exactly (Decimal, two places). No row dropped or duplicated.
- Nothing is called spending without her say-so; returns are never income.
- Every claim in an answer has evidence that opens real rows.
- No statement text, name, amount or passphrase in logs; transcripts are redacted.

## How to run
`make test-db-reset`, then the backend test (`pytest tests/test_real_statements_local.py`) and the
Playwright spec `frontend/tests/e2e/real-statements.spec.ts` (writes `test-results/real-ux/`).
