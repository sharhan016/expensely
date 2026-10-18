# PRD: Expense Analyzer MVP

**Status:** Draft  
**Version:** 0.1  
**Last updated:** 2026-10-18  
**Primary user:** A single authenticated user managing personal financial records  
**Technical reference:** `ARCHITECTURE.md`

## Product Summary

Expense Analyzer replaces manually maintained spreadsheets with a private, statement-first financial workspace. The user imports statements from multiple bank and credit-card accounts, confirms immutable bank movements, assigns financial meaning, tracks obligations involving people and employers, and reconciles each statement period.

The product must reduce manual investigation without presenting uncertain classifications or reconciliation causes as facts. Deterministic extraction, duplicate detection, classification, arithmetic, and reconciliation must work without an LLM.

## Goals and Proposed Success Measures

| Goal | Proposed MVP measure |
|---|---|
| Reduce statement-entry work | Supported statements import without manual transaction transcription |
| Preserve financial integrity | Re-importing the same records creates no duplicate ledger movements |
| Make uncertainty manageable | Unresolved and suggested classifications are visibly separated from confirmed classifications |
| Explain balances | Every dashboard or report total drills down to contributing transactions |
| Support reconciliation | Every supported statement period shows expected closing, actual closing, and exact difference |
| Protect private data | Statement contents, credentials, account numbers, and PDF passwords never appear in logs or external AI requests |

Quantitative adoption and time-saving targets require beta usage data.

## Product Principles

1. Imported bank movement and editable financial meaning are separate.
2. PostgreSQL-backed decimal calculations are authoritative.
3. Credit-card liabilities are not bank cash.
4. Suggestions are evidence, not authoritative records.
5. Ambiguous financial behavior remains unresolved until the user decides.
6. Every record and association is scoped to its authenticated owner.
7. Summary figures and detail views use the same backend calculations.

## MVP Non-Goals

- Multi-user household or organization collaboration.
- Mandatory LLM or paid extraction services.
- Financial advice, tax filing, or accounting compliance.
- Autonomous financial changes by a future investigative agent.
- Support for every bank layout.
- Claimed screenshot or scanned-PDF support until local OCR is verified.
- Native mobile applications.
- Currency conversion or consolidated multi-currency reporting until rules are approved.

## Confirmed Requirements

These requirements come from the established product brief, explicit user feedback, `README.md`, and `ARCHITECTURE.md`:

- Retain the Next.js/TypeScript frontend, FastAPI backend, and PostgreSQL database.
- Begin with statement import and support multiple accounts and periods.
- Process PDF, CSV, and text imports locally; keep protected-PDF passwords request-scoped.
- Keep bank movements immutable after confirmation; make meaning and associations editable and auditable.
- Support personal expense, business expense, income, internal transfer, loan given, loan repayment, entrusted funds, reimbursement, refund, and other/needs review.
- Treat credit cards as liabilities and exclude them from bank and available balances.
- Associate business expenses and reimbursements with an employer; support one default employer.
- Keep entrusted funds and loans person-specific and separate.
- Build trips from date-range candidates and group existing transactions without copying them.
- Reconcile per account and period; invalidate affected reconciliation after relevant changes.
- Detect duplicates and classify deterministically; never match solely by amount.
- Never send full statements to external AI services by default.
- Require authentication and server-side ownership checks.

## Feature Requirements

### 1. Bank Accounts and Statement Imports

**User goal:** Import one or more statements and have the application identify or create the correct account without requiring account setup first.

**Expected workflow**

1. Upload one or several supported files.
2. Validate file size, extension, signature, and ownership.
3. For a protected PDF, request the password without storing or logging it.
4. Extract institution, account identity and type, period, balances, and rows.
5. Bind to a reliably identified account; otherwise request confirmation.
6. Review warnings, extracted rows, duplicates, and classification suggestions.
7. Confirm selected rows atomically.
8. See the account and statement in account history.

**Business rules**

- A statement can bind only to an account owned by the user.
- Identity uses bank, account type, and stable account evidence; filename alone is insufficient.
- Unknown or ambiguous identities require user confirmation.
- Credit-card and deposit accounts are distinct types.
- Confirmation creates all selected ledger movements or none.
- Failed or cancelled imports cannot partially change the ledger.
- Parsers return canonical records without database writes.
- Unsupported layouts fail honestly and provide an available recovery path.

**Acceptance criteria**

- [ ] Each file in a batch has an independent status and review result.
- [ ] A recognized identity binds to the correct existing account.
- [ ] A reliable unseen identity can create a named account during confirmation.
- [ ] An ambiguous identity asks the user to select or create an account.
- [ ] A protected PDF can be retried without persisting its password.
- [ ] A failed file does not cause other files to be reported incorrectly.
- [ ] Confirmed movements and their account association survive reload.

**Error cases:** Unsupported type, invalid signature, excessive size, malformed rows, unsupported layout, unreadable or scanned PDF, incorrect password, ambiguous identity, storage failure, database failure, and cross-user account reference.

**Required automated tests:** Parser fixtures per advertised layout; type and size validation; protected-PDF retry; multi-file partial failure; ambiguous binding; account creation; confirmation rollback; file cleanup on failure; cross-user rejection; credit-card detection.

### 2. Transaction History and Classification

**User goal:** Review a unified ledger and classify transactions efficiently without processing every row one by one.

**Expected workflow**

1. View paginated transactions across all or selected accounts.
2. Search and filter by date, amount, purpose, category, merchant, person, employer, trip, and review state.
3. Review confident matches, confirmation-required suggestions, and unresolved items separately.
4. Apply or correct meaning individually or in bulk.
5. Optionally save a confirmed pattern as a reusable rule.
6. Review the audit history of meaning changes.

**Business rules**

- Source date, amount, description, reference, and imported balance are immutable after confirmation.
- Purpose, category, merchant, notes, and associations are separate fields.
- Amount may constrain a rule but cannot be its only evidence.
- Exact user rules precede normalized history and conservative built-in mappings.
- Suggestions record evidence and confidence.
- Other/needs review means unresolved, not an inferred final purpose.
- Required associations are validated server-side.

**Acceptance criteria**

- [ ] Transactions from multiple accounts have deterministic chronological order.
- [ ] The user can select and classify many unresolved rows in one action.
- [ ] A matching rule displays the proposed classification and evidence.
- [ ] Correcting meaning leaves immutable bank fields unchanged.
- [ ] A change within a reconciled period invalidates its reconciliation.
- [ ] The backend calculates correct running balances across pages and filters.

**Error cases:** Invalid purpose, unauthorized association, conflicting rules, missing rows in bulk updates, interrupted bulk update, stale edit, and missing required person or employer.

**Required automated tests:** Purpose validation; immutable-field protection; rule ordering and conflicts; no amount-only matching; atomic bulk update; pagination and ordering; normalized search; association ownership; reconciliation invalidation; frontend batch review.

### 3. Internal Transfers

**User goal:** Identify movements between owned accounts without counting them as income or spending.

**Expected workflow**

1. Classify a movement as an internal transfer.
2. Select or confirm its counterpart in another owned account.
3. Review candidates suggested from account, date, amount, reference, and description evidence.
4. See the pair as neutral in consolidated reports while retaining both account movements.
5. Resolve unmatched transfers later.

**Business rules**

- A completed transfer links two owned movements with opposing directions.
- Linked principal remains in each account ledger but is excluded from consolidated income and expense.
- Classification alone does not prove a counterpart exists.
- A movement cannot belong to multiple transfer links.
- Exchange rates, transfer fees, and unequal principal are unresolved.

**Acceptance criteria**

- [ ] Equal opposite movements in two owned accounts can be linked.
- [ ] Linked principal is excluded from consolidated income and expense totals.
- [ ] Each account ledger retains its debit or credit.
- [ ] An unmatched transfer remains visibly incomplete.
- [ ] An already linked movement cannot be linked again.

**Error cases:** Same-sign pair, different owner, duplicate link, missing counterpart, changed classification, fee, foreign currency, and dates outside the suggestion tolerance.

**Required automated tests:** Valid pair; same-sign rejection; ownership enforcement; one-to-one constraint; consolidated neutrality; per-account balance preservation; unlink and relink; unmatched state.

### 4. Personal and Business Expenses

**User goal:** Separate personal spending from work spending and track recoverable business costs without distorting income.

**Expected workflow**

1. Classify an outgoing movement as personal or business.
2. For business spending, apply the sole/default employer when appropriate.
3. Show an employer selector only when another choice is meaningful.
4. Optionally assign merchant, category, notes, and receipt metadata.
5. View personal and business spending separately.

**Business rules**

- Business expense requires an owned employer.
- A sole/default employer may be applied automatically and shown transparently.
- Reclassification updates reports but never changes bank movement.
- Refund and reimbursement are distinct meanings.
- Expense totals use qualifying outgoing movements and documented sign conventions.

**Acceptance criteria**

- [ ] With one default employer, business classification associates it automatically.
- [ ] With several employers, the user can select another company.
- [ ] Without an employer, business classification is blocked or visibly incomplete.
- [ ] Personal and business totals drill down to their transactions.
- [ ] Reclassification updates all affected views consistently.

**Error cases:** No employer, unauthorized employer, positive movement classified as expense, refund ambiguity, archived employer, and conflicting bulk classification.

**Required automated tests:** Default-employer assignment; multiple-employer choice; ownership; missing-employer validation; sign edge cases; report-to-detail equality; reclassification propagation.

### 5. Shared Expenses and Money Owed by Other People

**User goal:** Understand money connected to another person and whether it belongs to them, was paid for them, or is owed back.

**Expected workflow**

1. Create a person.
2. Link existing transactions to that person with explicit financial meaning.
3. Open a person detail view showing transactions and separate balances.
4. Correct or remove associations without deleting movements.
5. Drill from dashboard/person totals to source transactions.

**Business rules**

- Entrusted money and loans are separate sub-ledgers.
- Entrusted receipts increase that person’s balance.
- Payments made on that person’s instruction reduce their entrusted balance and are not automatically personal expenses or loans.
- A loan given increases money owed to the user; repayment reduces it.
- Generic shared-expense and split-bill allocation are not yet defined.
- A person association alone does not determine financial meaning.

**Acceptance criteria**

- [ ] Entrusted receipts and later instructed payments produce the correct person balance.
- [ ] Loans appear separately from entrusted funds.
- [ ] Person totals equal their linked transaction sub-ledgers.
- [ ] Removing a link recalculates totals without changing the movement.
- [ ] Undefined shared spending is never automatically labelled as a loan.

**Error cases:** Missing or unauthorized person, entrusted payment exceeding held amount, ambiguous shared purchase, archiving a person with an active balance, and incorrect sign.

**Required automated tests:** Entrusted receipt/payment; person isolation; loan separation; ownership; relink/unlink; dashboard-to-detail equality; negative-balance behavior after policy approval.

### 6. Loans, Repayments, and Reimbursements

**User goal:** Track money owed by people and employers, including partial settlements, without counting settlements as new income.

**Expected workflow**

1. Classify money given as a loan and associate a person.
2. Classify received money as repayment and allocate it to that person’s outstanding loan.
3. Classify personal-account work spending as business expense for an employer.
4. Classify employer receipts as reimbursements.
5. Allocate one reimbursement across one or more expenses, including partial settlement.
6. View outstanding balances and settlement history.

**Business rules**

- Loans require a person; business expenses and reimbursements require an employer.
- Repayments reduce principal and are not income.
- Reimbursements settle expenses and are not income or duplicate expenses.
- Allocation records provide settlement traceability.
- Overpayment, interest, fees, and write-offs are unresolved.

**Acceptance criteria**

- [ ] Partial repayment reduces but does not close principal.
- [ ] Full repayment closes the matching outstanding principal.
- [ ] Partial reimbursement leaves a visible remaining receivable.
- [ ] One reimbursement can settle several expenses.
- [ ] Settlements are not counted as income.
- [ ] Every outstanding total traces to origin and settlement movements.

**Error cases:** Repayment without person, reimbursement without employer, allocation beyond receipt or outstanding expense, overpayment, negative outstanding value, and cross-user allocation.

**Required automated tests:** Partial/full repayments; partial/multi-expense reimbursement; no income duplication; allocation limits; ownership; sign conventions; refund distinction; overpayment after policy approval.

### 7. Trip-Based Expense Tracking

**User goal:** Create a trip from travel dates and review potentially related existing transactions, including advance bookings.

**Expected workflow**

1. Enter trip name and travel start/end.
2. Optionally widen the purchase window for advance or delayed purchases.
3. Review transactions within the candidate window.
4. Include, disregard, or leave candidates pending.
5. View included transactions and total.
6. Revisit decisions later.

**Business rules**

- Travel dates are required and ordered.
- The purchase window contains the travel dates.
- Trips reference existing transactions and never copy them.
- A transaction belongs to at most one trip.
- Trip decisions happen in the trip workflow, not through a permanent “No Trip” ledger field.
- Totals aggregate included transactions only.

**Acceptance criteria**

- [ ] Creating a trip retrieves owned transactions within its purchase window.
- [ ] Including a candidate updates the total without duplicating movement.
- [ ] Disregarded candidates remain excluded after reload.
- [ ] A transaction in another trip is unavailable.
- [ ] Expanding the window can reveal advance bookings.

**Error cases:** Invalid date range, purchase window excluding travel dates, no candidates, transaction already assigned, unauthorized transaction, and later correction/deletion.

**Required automated tests:** Date validation; boundary inclusion; persistent include/exclude/pending states; one-trip constraint; aggregation; no duplication; ownership; empty state.

### 8. Reconciliation Periods and Finalized History

**User goal:** Prove that an account ledger matches a statement period and preserve reliable historical status.

**Expected workflow**

1. Select an account and inclusive period.
2. Enter or accept detected opening and closing balances.
3. Calculate expected closing from confirmed movements.
4. Display the difference and supporting evidence counts.
5. Mark the period balanced when the difference is zero.
6. Preserve reconciliation history.
7. Invalidate and rerun affected history after relevant changes.

**Business rules**

- Reconciliation belongs to one owned account and period.
- `expected closing = opening balance + confirmed movements`.
- `difference = statement closing - expected closing`.
- Evidence may be shown, but unsupported causes cannot be asserted.
- Credit-card signs follow documented liability conventions.
- Relevant period changes invalidate reconciliation.
- Adjacent periods should be checked for closing-to-opening continuity.
- Finalized locking behavior is unresolved; confirmed behavior is invalidation, not irreversible locking.

**Acceptance criteria**

- [ ] Exact decimal arithmetic produces zero difference for a balanced period.
- [ ] A non-zero period shows exact difference and evidence without invented cause.
- [ ] History is account- and period-specific.
- [ ] A relevant edit invalidates status with an explanation.
- [ ] The user can rerun an invalidated period.
- [ ] Adjacent discontinuity is visibly flagged.

**Error cases:** Reversed dates, unauthorized account, overlapping reconciliation, missing/invalid balance, changed imported row, missing or duplicate movement, and adjacent discontinuity.

**Required automated tests:** Balanced/difference calculations; decimal precision; uniqueness; ownership; invalidation and rerun; credit-card signs; continuity; evidence counts; absent cause without proof.

### 9. Duplicate Detection and Overlapping Statements

**User goal:** Re-import repeated or overlapping statements without duplicating ledger activity.

**Expected workflow**

1. Detect period overlap before confirmation.
2. Mark possible duplicate rows with evidence.
3. Deselect likely duplicates by default while preserving review.
4. Recheck canonical identity during confirmation.
5. Report imported and skipped counts.

**Business rules**

- Canonical duplicate identity is scoped to user and account.
- Reference, date, amount, and normalized description contribute to identity.
- Amount alone is never enough.
- Different references remain distinct unless stronger evidence proves otherwise.
- Period overlap is a warning, not proof that all rows are duplicates.
- Database constraints and transactional confirmation are the final protection.

**Acceptance criteria**

- [ ] Re-importing the same confirmed statement creates no duplicate movement.
- [ ] Overlapping periods are warned before confirmation.
- [ ] Legitimate same-date/same-amount transactions with distinct references remain separable.
- [ ] Confirmation reports skipped duplicates.
- [ ] Concurrent confirmation cannot bypass uniqueness protection.

**Error cases:** Missing reference, description formatting changes, ambiguous account, identical legitimate movements, concurrent confirmation, and overlap without matching rows.

**Required automated tests:** Exact re-import; overlapping import; normalization; distinct-reference collision; missing references; account/user scoping; concurrent constraint enforcement; warning rather than automatic rejection.

### 10. Account Ledgers, Timelines, Category Reports, and Person Views

**User goal:** Understand where money moved, why, and who or what it relates to from summary through detail.

**Expected workflow**

1. Open an account ledger with running balances and statement periods.
2. View a unified chronological timeline.
3. Filter reports by category and purpose.
4. Open person views for entrusted and loan history.
5. Drill from every total to contributing transactions.
6. See bank assets separately from credit-card liabilities.

**Business rules**

- Backend reporting services calculate authoritative totals.
- Running balance starts from account opening and applies movements deterministically.
- Credit-card liabilities display separately from bank cash.
- Transfers are neutral only in consolidated income/expense reporting.
- Trip, person, category, and employer reports reference the same movements.
- Filters cannot silently change the meaning of displayed totals.

**Acceptance criteria**

- [ ] Running balances match imported balances where supplied.
- [ ] Account filters do not modify stored records.
- [ ] Bank balance excludes credit-card liability.
- [ ] Category and person totals equal drill-down transactions.
- [ ] Same-date ordering is stable.
- [ ] Empty reports explain how relevant data is created.

**Error cases:** Missing opening balance, same-day ordering ambiguity, stale aggregate, deleted association, unsupported currency, and pagination boundary.

**Required automated tests:** Running balances; deterministic order; bank/card separation; transfer neutrality; report-to-ledger equality; filtering/pagination; person/category drill-down; empty states.

### 11. Error Handling and Correction Workflows

**User goal:** Understand failures, recover safely, and correct financial meaning without damaging imported evidence.

**Expected workflow**

1. Receive a specific, non-sensitive error and next action.
2. Retry password, parsing, import, or save where safe.
3. Edit meaning and associations after import.
4. See when a correction invalidates reconciliation or changes reports.
5. Confirm destructive actions and retain required history.

**Business rules**

- Errors never expose statement contents, credentials, storage paths, or internals.
- Retriable and terminal states are distinct.
- Failed confirmation creates no partial movements.
- Ordinary corrections change meaning, not source bank evidence.
- Financial-history corrections are auditable.
- File and data retention behavior must be approved before release.

**Acceptance criteria**

- [ ] A wrong PDF password provides a retryable prompt without losing the import.
- [ ] Unsupported statements give a truthful explanation and alternative.
- [ ] Failed confirmation leaves the ledger unchanged.
- [ ] Classification corrections record what changed and when.
- [ ] Reconciliation invalidation identifies period and reason.
- [ ] Unauthorized resources return safe errors.

**Error cases:** Network interruption, unavailable storage, rollback, stale browser state, invalid association, duplicate submission, expired session, parser timeout, and corrupted file.

**Required automated tests:** Retry; transactional rollback; idempotent duplicate submission; safe error content; expired session; cross-user isolation; audit creation; invalidation message; frontend loading/error/recovery states.

### 12. Authentication, Privacy, and Data Protection

**User goal:** Use a private financial workspace knowing records and statements are accessible only to their authenticated owner.

**Expected workflow**

1. Register with email, display name, and password.
2. Receive a secure session.
3. Sign in/out and recover from expiry.
4. Access only owned records and files.
5. Export or delete account data when lifecycle functionality is available.

**Business rules**

- Passwords are hashed and never logged.
- Sessions use secure, HTTP-only, expiring cookies with environment-appropriate flags.
- Cookie-authenticated writes require CSRF protection or an approved equivalent.
- Authentication is rate-limited.
- Every API operation validates ownership, including referenced IDs.
- Uploads are private, path-safe, and bounded by size, pages, time, and memory.
- PDF passwords are never persisted or logged.
- Full statements are not sent to an external LLM by default.
- Production cannot use development session secrets.
- Backup, restore, deletion, and recovery procedures are documented and exercised.

**Acceptance criteria**

- [ ] Registration rejects duplicate emails and weak passwords.
- [ ] Invalid or expired sessions cannot access financial data.
- [ ] One user cannot read or link another user’s resources.
- [ ] Logout removes or invalidates the browser session.
- [ ] Production rejects unsafe secrets and cookie settings.
- [ ] Account deletion handles private stored files according to policy.

**Error cases:** Duplicate registration, brute-force login, expired/tampered cookie, CSRF attempt, cross-user ID, path traversal, malicious PDF, resource exhaustion, backup failure, and partial deletion.

**Required automated tests:** Registration/login/logout/expiry; password hashing; throttling; CSRF defense; cross-user isolation for every resource; secure cookie flags; unsafe-secret rejection; path traversal; resource limits; log redaction; export/deletion lifecycle.

## Assumptions Requiring Validation

These are working assumptions, not approved financial rules:

1. The first beta remains single-user per installation, while records retain `user_id` ownership.
2. INR is the only authoritative reporting currency for the MVP.
3. One employer can be default while additional employers remain available.
4. Positive amounts enter an account and negative amounts leave it, with credit-card liability conventions documented per parser.
5. High-confidence suggestions remain reviewable; automatic acceptance thresholds need approval.
6. One person can have both entrusted-fund and loan activity as separate balances.
7. Source statements remain privately stored after confirmation until retention policy approval.
8. When no timestamp exists, same-day order uses imported order or stable internal ID; its acceptability for authoritative running balances needs confirmation.

## Unresolved Questions

1. Does “shared expense” mean split bills, money advanced for another person, joint ownership, or all three?
2. Can entrusted balance become negative, and does that deficit become money owed?
3. How are loan interest, fees, write-offs, and overpayments represented?
4. How is reimbursement exceeding outstanding business expense treated?
5. Must refunds link to original expenses, including partial or multi-expense refunds?
6. What tolerance is allowed for transfer counterpart suggestions involving delay or fees?
7. Are foreign-currency transactions excluded, or preserved without consolidated conversion?
8. Does finalized reconciliation lock meaning, require explicit reopening, or invalidate automatically?
9. Can statement periods legitimately overlap for one account?
10. What is the statement-file retention and deletion period?
11. What data-export format is required?
12. Is local OCR required for MVP approval?
13. Which banks and statement layouts form the supported launch set?
14. Should card payments be suggested automatically as transfers between deposit and card accounts?
15. Which built-in merchant/category mappings are acceptable, and who maintains them?
16. How much audit history must be visible to the user?
17. What quantitative targets define beta approval?

## Current Repository Evidence and Gaps

The repository contains working slices for authentication, private uploads, multi-file staging, protected-PDF retry, YES BANK credit-card parsing, generic tabular parsing, account creation/binding, classification rules, people, employer reimbursement summaries, trip candidate review, reconciliation, theme settings, and separation of bank funds from card liabilities.

Important gaps against this PRD:

- `Transaction` still combines bank movement and editable meaning.
- There is no transfer-link model or workflow.
- There is no reimbursement-allocation model.
- Category and merchant models/reports are absent.
- Classification evidence, versions, and audit history are absent.
- Association ownership is not comprehensively validated on update paths.
- Reconciliation does not persist all expected snapshots, fingerprints, or invalidation reasons.
- Ledger retrieval is fixed to 500 rows and running balance is calculated in the frontend.
- Generic shared-expense behavior is undefined.
- File lifecycle, OCR, export/deletion, CSRF, throttling, production-cookie hardening, backup/recovery, PostgreSQL integration tests, and critical Playwright coverage remain incomplete.

## Beta Release Gate

The MVP is ready for beta only when:

- A fresh installation can register, import a supported statement, review and confirm it, classify transactions, and reconcile the period.
- Re-importing the same or overlapping statement cannot create duplicate movements.
- Bank funds, credit-card liabilities, entrusted funds, loans, and employer receivables are separately correct and traceable.
- Every association enforces ownership.
- Critical parser, financial, API, and browser workflows pass automated tests.
- Unsupported formats and unresolved classifications are presented honestly.
- Privacy, retention, backup, recovery, and production-security procedures are documented.
- Every unresolved question needed by a shipped workflow has an approved answer.
