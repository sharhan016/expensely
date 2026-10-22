# Expense Analyzer: build the agent-first financial workflow

## 0. Product direction

Build a private, conversation-first financial assistant for one person. She should be able to open the assistant, upload one or more statements, and ask ordinary questions without first configuring accounts, rules, or spreadsheets.

The assistant should do the operational work: identify the document and account, extract and reconcile transactions, connect related movements, learn recurring transaction flows, and explain what happened. It should ask for human input only when evidence is genuinely ambiguous or a decision would materially affect financial meaning.

This is broader than an upload-to-report screen, but it must be delivered incrementally. The first complete vertical slice is:

`conversation + upload -> identify source -> extract -> reconcile -> dedupe -> understand flows -> report -> learn from corrections`

## 1. Existing foundation to preserve

Audit the repository before changing it. Preserve and extend sound behavior rather than rebuilding it.

The current repository already contains useful foundations:

- Next.js, FastAPI, PostgreSQL, and authenticated owner-scoped records.
- Immutable imported movements separated from editable transaction meaning.
- Statement imports, account binding, reconciliation, duplicate detection, and classification rules.
- A YES BANK credit-card parser and an explicit `credit_card` account kind.
- Separation of credit-card liabilities from bank cash in dashboard calculations.
- People, employers, loans, entrusted funds, reimbursements, trips, and meaning-change history.

Important current gaps for this workflow:

- No durable merchant/counterparty identity or alias model.
- No first-class group for repeated flows to the same person or merchant.
- No transfer-link model connecting a bank payment to the corresponding credit-card payment.
- History matching predicts mainly purpose; it does not learn the full meaning of a flow.
- No conversation/orchestration layer with an explicit autonomy policy.
- The YES BANK parser recognizes a known credit-card layout, but current-account and other statement layouts still require evidence-backed parser support.

## 2. Non-negotiable financial rules

- Use `Decimal` or integer paise. Never use floating point for authoritative money logic.
- Keep every imported movement immutable and traceable to its source statement.
- Never merge, delete, or replace ledger rows merely because they look similar.
- Distinguish three different concepts:
  1. **Duplicate:** the same source movement imported again; skip the new copy.
  2. **Related movement:** two separate rows representing one flow, such as a bank payment and a card payment credit; link both rows.
  3. **Repeated flow:** separate real transactions involving the same counterparty or purpose; group them for understanding and reporting.
- Do not infer that a payment is a loan merely because it went to a person. A person link, a purpose, and an obligation are separate facts.
- Amount alone is never enough to identify a duplicate, counterparty, transfer, loan, or category.
- Every computed total must drill down to the original movements.
- Reconcile each statement before treating it as complete. Parsing or reconciliation failures remain visible and never silently post partial data.
- A suggestion is not a fact. Record evidence, confidence, model/rule version, and whether a decision was inferred, confirmed, or corrected.
- Process statement contents locally by default. Do not send raw statements, descriptions, names, account numbers, or balances to an external AI service.
- Never log statement contents, transaction descriptions, names, amounts, passwords, or account numbers.

## 3. Account and statement identification

The user should not have to declare “current account” or “credit card” before upload. Detect it from document evidence.

For every upload, determine:

- institution and statement layout;
- financial instrument type: current account, savings/deposit account, or credit card;
- stable masked identity, such as last four digits plus institution and instrument type;
- statement period and balance semantics;
- currency;
- whether the document is a new account, an existing account, or ambiguous.

Use labels, headings, account/card number patterns, debit/credit conventions, balance fields, and known layout signatures. Filename is weak evidence and cannot establish identity.

The parser registry should select by document evidence, not only bank name, because one bank can issue materially different current-account, savings-account, and credit-card formats.

If identity is reliable, bind automatically. If two existing accounts remain plausible, pause and ask one focused question with the candidates. Remember the confirmed binding for later uploads.

## 4. Credit-card behavior

Treat a credit card as a liability account, not as bank cash.

- A card purchase is an expense candidate and increases liability.
- A card payment or credit from an owned bank account reduces liability. Once linked to its bank-side debit, the pair is an internal transfer and must not be counted as either new spending or income.
- A merchant refund or reversal offsets the related card purchase when supported by evidence.
- Interest, annual fees, late fees, taxes, and similar charges are expenses, not transfers.
- Cash advances, EMI conversions, instalment principal, instalment interest, partial payments, overpayments, and foreign-currency adjustments need explicit handling. Leave them unresolved when the statement does not provide enough evidence.
- Card “available credit”, “total amount due”, “minimum amount due”, and statement balance are different values. Store and label them separately; never substitute one for another.
- Reconcile using the sign and balance semantics of the specific card layout.

When both sides are available, propose a card-payment link using all available evidence: opposite signs, equal principal, compatible dates, owned source/destination accounts, references, narration tokens, and card identity. A date/amount match alone is not proof. Prevent a movement from belonging to more than one completed link.

## 5. Understanding transaction flows

After validated rows are staged, derive a normalized view without changing the source rows.

Extract, when supported:

- payment rail or channel, such as UPI, NEFT, IMPS, RTGS, card, cheque, cash, fee, or interest;
- counterparty or merchant display name;
- stable identifiers such as UPI handle, merchant/acquirer token, bank reference, or masked account/card hint;
- direction;
- likely purpose, category, person, employer, trip, or obligation;
- links to a transfer, refund/reversal, reimbursement, repayment, or other related movement.

Create durable counterparty identities and aliases. For example, multiple narrations that contain different reference numbers but the same reliable UPI handle may map to one counterparty. Preserve both the raw description and the normalized evidence.

Create analytical flow groups that reference transactions instead of copying or merging them. A group should answer questions such as:

- “How much did I pay this person?”
- “How much did I spend at this shop?”
- “Was this money a personal expense, entrusted payment, reimbursement, loan, or transfer?”
- “What is still owed, and what has been settled?”

A counterparty total alone must not imply purpose. The same person may receive a loan, a repayment, a shared expense, and an entrusted-fund payment at different times. Group by counterparty and financial meaning, with separate subtotals and unresolved items.

## 6. Learning and autonomy

The assistant learns from explicit confirmations and corrections, not from repeatedly guessing.

Use this evidence order:

1. Exact user-confirmed rule with compatible account, direction, and identifiers.
2. Confirmed stable counterparty alias or owned-account/card identity.
3. Exact normalized confirmed history.
4. Strong repeated pattern with compatible purpose and associations.
5. Conservative built-in mapping.
6. Unresolved.

Persist learned decisions as versioned rules with provenance: what the user confirmed, when, the evidence fields used, scope, and exceptions. Do not train on unresolved suggestions or on the assistant’s own unconfirmed output.

Autonomy policy:

- **Automatic:** safe document detection, extraction, arithmetic, known-account binding, exact dedupe, and exact confirmed rules.
- **Automatic but reversible and disclosed:** high-confidence grouping or labeling that changes no money and can be traced to a confirmed rule.
- **Ask once, then remember:** ambiguous account binding, a new counterparty alias, or a repeated flow whose meaning is stable after confirmation.
- **Always ask before committing:** ambiguous loans, entrusted money, reimbursements, shared expenses, unmatched transfers, or any classification that changes an obligation balance.

Corrections must update future behavior without rewriting earlier source evidence. If a correction should apply retrospectively, show the affected transactions and ask for confirmation before bulk-changing their meanings.

## 7. Conversation-first workflow

Implement the workflow as deterministic services coordinated by an agent. Financial calculations and persistence stay in plain code; the language model may choose tools, ask focused questions, and explain results, but may not invent or directly calculate authoritative figures.

Suggested stages:

1. **Receive:** accept the user’s message and one or more statement files.
2. **Inspect:** detect institution, instrument type, layout, period, and account identity.
3. **Extract:** produce canonical immutable rows and structural diagnostics.
4. **Validate:** check statement arithmetic and running balances using layout-specific semantics.
5. **Bind:** attach to the known account or ask one account question.
6. **Dedupe:** remove only proven re-imports from the proposed posting set.
7. **Resolve entities:** normalize counterparties, merchants, rails, and stable identifiers.
8. **Connect flows:** propose transfers, card payments, refunds, reimbursements, repayments, and repeated-flow groups.
9. **Apply knowledge:** run confirmed rules and history-based suggestions under the autonomy policy.
10. **Post:** atomically confirm eligible movements and links; leave uncertain meanings open.
11. **Respond:** give a concise conversational summary with drill-down actions and only the questions that block useful understanding.
12. **Learn:** store confirmed corrections and aliases with audit history.

Every stage emits a user-friendly progress event. The workflow must be resumable after a question or recoverable failure.

## 8. Agent response

The assistant should answer in plain language, for example:

> I found a YES BANK credit-card statement ending 9405 for 1–31 August. All 46 rows reconcile. You spent ₹X across Y purchases, received ₹Z in refunds, and made one card payment of ₹P. I linked that payment to the matching debit from your YES current account, so it is not counted as spending twice. I recognized 10 payments to Asha totaling ₹T: 8 match your confirmed entrusted-payment rule, while 2 still need a purpose.

The response should include:

- what was uploaded and which account it belongs to;
- period and “balance valid as of” context;
- reconciliation result;
- new transactions, proven duplicates skipped, and unresolved rows;
- spending and incoming-money summaries by financial meaning;
- repeated counterparties/merchants and totals, with drill-down;
- linked and unmatched transfers, especially card payments;
- obligations affected: loans, reimbursements, and entrusted balances;
- the smallest useful set of questions.

Never say “you spent” when the amount may be a transfer, loan, entrusted payment, reimbursement, or unresolved movement.

## 9. Domain additions

Introduce these concepts incrementally and migration-safely:

- `account.instrument_type` with an enum rather than an unrestricted string.
- `statement_layout` or parser/version evidence on each import.
- `counterparty` and `counterparty_alias`, owner-scoped and auditable.
- merchant/category fields separated from purpose.
- `transaction_link` with typed, one-to-one or allocation-aware relationships such as internal transfer, card payment, refund/reversal, loan repayment, and reimbursement.
- `transaction_group` plus membership for analytical repeated-flow groups; membership never changes ledger identity.
- structured `classification_evidence`, confidence, rule version, and decision source.
- `agent_action` or proposal/audit record capturing proposed, accepted, rejected, reverted, and automatically applied actions.

Do not encode every relationship as a single `purpose` enum or a free-text note. Keep source movement, counterparty, purpose, category, responsibility, obligation, and relationship links distinct.

## 10. First delivery slices

### Slice 1: trustworthy YES BANK intake

- Detect YES BANK current/deposit accounts versus credit cards from evidence.
- Parse supported layouts, bind accounts, stage rows, and reconcile them.
- Preserve existing dedupe and immutable-ledger behavior.
- Report unsupported YES BANK layouts honestly with privacy-safe diagnostics.

### Slice 2: counterparty understanding

- Extract normalized counterparties and identifiers.
- Group repeated flows without merging rows.
- Show “paid to / received from” totals separated by meaning and unresolved status.
- Let the user confirm an alias or meaning once and reuse it safely.

### Slice 3: credit-card payment linking

- Match card-payment credits to owned-bank debits using multi-field evidence.
- Keep both movements, mark the link, and exclude the pair from consolidated income/expense.
- Handle unmatched and partial payments visibly.

### Slice 4: conversational operator

- Add resumable tool-driven orchestration around the deterministic services.
- Provide progress, summaries, focused questions, and correction handling.
- Add explicit action permissions and complete audit history.

### Slice 5: broader financial memory

- Extend learned flows to loans, entrusted funds, reimbursements, refunds, merchants, categories, and trips.
- Add reversible retrospective proposals rather than silently rewriting history.

## 11. Required tests and acceptance scenarios

- A YES BANK current-account statement and YES BANK credit-card statement are detected correctly without asking the user when identity evidence is sufficient.
- Two accounts from the same bank never bind solely from bank name or filename.
- Re-uploading an overlapping statement posts no duplicate rows.
- Ten genuine payments to the same person remain ten ledger movements and appear in one counterparty flow with the correct total.
- Similar descriptions for two different UPI handles do not collapse into one counterparty.
- A person flow with both a loan and personal payments shows separate subtotals.
- A bank debit and corresponding card-payment credit can be linked, remain visible in both ledgers, and contribute zero to consolidated income/expense.
- A card purchase is counted once as spending; paying the card does not count it again.
- Card refunds, reversals, fees, interest, partial payments, and unmatched payments follow the rules above.
- No automatic suggestion creates or changes a loan, entrusted balance, or reimbursement allocation without the required evidence and approval.
- Correcting a counterparty or meaning improves later suggestions while leaving raw descriptions unchanged.
- The full workflow works without an external LLM; an optional LLM receives only the minimum redacted context.
- Logs and failure diagnostics contain no transaction content, names, amounts, account numbers, card numbers, or PDF passwords.

## 12. Definition of done for the first agent-first milestone

The milestone is complete when she can open the assistant, upload a supported YES BANK account or credit-card statement, and receive a reconciled conversational explanation. The system correctly identifies the financial instrument, preserves every genuine movement, skips proven duplicates, groups repeated counterparties without conflating their meanings, links a card payment when both sides are present, avoids double-counting, and asks only for decisions it cannot safely make from confirmed evidence.
