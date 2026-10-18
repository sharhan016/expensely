# Expense Analyzer Interface Audit and Redesign Proposal

## Audit scope

Reviewed the existing Next.js interface, shared shell, every top-level route, desktop
layout, 390 × 844 mobile layout, keyboard focus behavior, loading/empty/error components,
forms, tables, theme controls, and the login/register flow.

This document is the approval gate. No business logic, API contract, financial rule, or
database behavior is included in the redesign scope.

## Product and audience

Expense Analyzer is a private operational finance workspace for people who need to turn
bank statements into a trusted ledger while separating bank movement from financial
meaning. Its primary interaction is repeated review: scan balances, resolve transactions,
confirm imports, and verify reconciliation. The interface should optimize for confidence,
comparison, and repeat use—not marketing spectacle.

## What already works

- The dark navy navigation, restrained cobalt accent, IBM Plex type, and monospaced money
  establish a credible finance-tool foundation.
- The dashboard's bank balance − entrusted funds = available funds equation explains an
  important product idea unusually well.
- The import flow preserves review before ledger confirmation and provides a visible file
  picker in addition to drag and drop.
- The layout uses one icon family consistently and most form fields have visible labels.
- Primary routes have stable URLs and the active route is visually marked.
- Desktop keyboard focus is visible on native form controls.

## Priority findings

### Critical — state and trust

1. Data failures can look like valid financial state. The dashboard suppresses fetch
   errors and renders zero balances plus “Signed in as there.” The import page similarly
   turns an account-fetch failure into an empty account list. In a finance product, a
   failed request must never be visually indistinguishable from a true ₹0 balance or an
   empty ledger.
2. Unauthenticated workspace routes render the shell instead of consistently guiding the
   user to sign in. This creates a convincing but untrustworthy blank workspace.
3. The global search, notification control, and keyboard shortcut are visible but do not
   provide working interactions. Non-functional controls reduce confidence in every other
   action.
4. Destructive rule deletion has no confirmation or recovery feedback. Async create,
   toggle, delete, and theme-save failures are inconsistently surfaced.

### High — responsive navigation and task flow

1. At 390 px, all eight desktop navigation links become a two-column block above every
   page. The shell consumes roughly the first 270 px before task content begins, forcing
   repeat users to scroll past navigation on every route.
2. Hiding the import statement list below 950 px removes the way to switch between files
   in a multi-statement review. A responsive redesign must preserve this control as tabs
   or a select, not remove it.
3. Data tables rely on horizontal scrolling without a mobile reading strategy. Critical
   identity and action columns can move off-screen, and row actions are not anchored.
4. Page actions and headings stack with large gaps on mobile, while some action groups and
   inline forms remain visually dense. Priority changes unpredictably between screens.
5. The settings rule form opens inline without a Cancel action, clear expanded state, or
   focus management. The “New rule” action remains visible as though nothing changed.

### High — accessibility

1. Much of the interface uses 8–13 px text, including metadata, table headings, helper
   text, navigation, statuses, and form labels. This is below a comfortable mobile and
   desktop reading baseline and will degrade under text scaling.
2. Many controls are 30–40 px high; mobile touch targets do not consistently reach 44–48 px.
3. There is no skip link, and the main content is not an explicitly focusable landmark for
   route changes.
4. The theme panel behaves visually like a dialog but lacks dialog semantics, focus trap,
   Escape handling, outside-click dismissal, and focus restoration.
5. Loading animation does not respect `prefers-reduced-motion`, and status dots/colors are
   sometimes used without an equally prominent text status.
6. Generic errors such as “Something needs attention / Not Found” do not explain what
   failed or provide a retry path. Errors are not announced as alerts.
7. Icon-only actions have accessible names in several places, but persistent tooltips and
   pressed/selected semantics are inconsistent.

### Medium — hierarchy, density, and copy

1. The visual system is split across several highly compressed one-line stylesheets. It
   has repeated raw sizes and no explicit semantic scale for type, spacing, touch targets,
   elevation, focus, or state colors.
2. Almost every content group uses the same white bordered rectangle. This weakens
   hierarchy and makes summaries, forms, errors, and data regions feel equivalent.
3. The top bar spends prominent space on an inert search field and two small icon buttons,
   while high-frequency page controls live farther from their data.
4. Helpful but repeated explanatory subtitles make operational screens taller. Returning
   users need current state, filters, and actions more than aspirational narration.
5. Empty dashboard tables show headers with no explanation or next action. Other empty
   states are more helpful but inconsistent in layout and tone.
6. Selected, hover, pressed, disabled, loading, success, and destructive states do not use
   one consistent interaction language.

## Proposed visual direction: “Ledger Desk”

A calm, daylight-first financial workbench: precise like a bank ledger, warm enough for
personal use, and dense without becoming cramped.

### Visual character

- Preserve the navy and cobalt identity, but replace flat gray with a warmer paper surface.
- Use thin rules and restrained elevation to evoke a well-organized ledger, not a dashboard
  made entirely of cards.
- Keep IBM Plex Sans for interface text and IBM Plex Mono for money, dates, account hints,
  and reconciliation evidence.
- Use an 8 px spacing rhythm, 16 px minimum mobile body/input text, 14 px secondary text,
  and a compact but readable desktop table scale.
- Reserve accent for primary actions, active navigation, focus, and selected data. Use
  semantic green, amber, and red only for financial/status meaning and always pair them
  with text or icons.
- Use 6–10 px radii rather than mixing tiny rectangles and circles arbitrarily.
- Use short opacity/transform transitions for drawers, sheets, and disclosure; respect
  reduced motion.

### Memorable product detail

Make the “bank − entrusted = available” relationship a recurring ledger equation. On the
dashboard it is the primary summary; in relevant detail views the same visual grammar
connects totals to their underlying transactions.

## Proposed information architecture

### Desktop

- Keep the persistent sidebar, but group destinations into **Overview**, **Ledger**, and
  **Manage** sections to reduce an undifferentiated eight-item list.
- Replace inert global controls with only working controls. If search is not implemented,
  remove the fake command field for now.
- Give each page a compact title row: route context, useful current state, and one primary
  action. Place filters directly above the data they affect.
- Use full-width ledger surfaces for transaction-heavy routes; use cards only for summary
  metrics, selectable entities, and distinct state callouts.

### Mobile

- Replace the full eight-link header block with a compact top app bar and menu drawer.
- Keep the primary page action reachable in the title region; allow sticky action placement
  only for consequential review/confirm flows.
- Convert wide tables into responsive rows/cards showing the decision-critical fields, with
  an expandable detail area for secondary fields. Preserve a table view at larger widths.
- Convert the import statement sidebar into an accessible file selector or horizontal tab
  list so multi-file review remains possible.

## Component and state redesign

1. **App shell:** skip link, semantic landmarks, grouped desktop navigation, mobile drawer,
   real user identity, working theme dialog, and removal of inert controls.
2. **Page header:** consistent title/action hierarchy with optional contextual status and
   no redundant prose.
3. **Buttons and inputs:** 44 px minimum target, visible focus ring, standardized primary,
   secondary, quiet, and destructive variants, plus loading and disabled states.
4. **Workspace state:** distinct skeleton, empty, authentication, offline/failure, and
   success feedback; errors include retry or next action and use live-region semantics.
5. **Data display:** tabular numeric alignment, sticky readable headers on desktop,
   responsive transaction rows on mobile, and explicit empty-table states.
6. **Forms:** grouped fields, clearer required/optional treatment, inline recovery errors,
   Cancel actions for disclosed forms, and focus movement when forms open or fail.
7. **Import review:** persistent statement switching, clear extraction/account status,
   stronger duplicate/review evidence, sticky confirmation summary, and safe mobile actions.
8. **Dashboard:** retain the ledger equation, reduce repeated card chrome, add a meaningful
   empty/error state, and keep totals visibly different from navigational/status signals.

## Screen-by-screen redesign scope

- **Login/register:** retain the split identity panel on wide screens; shorten the mobile
  intro, add password visibility, submit loading, clearer inline errors, and a less dominant
  marketing block on return visits.
- **Dashboard:** fix truthful loading/error/empty behavior, strengthen numeric hierarchy,
  simplify status tiles, and provide an actionable recent-activity empty state.
- **Accounts & Import:** prioritize accounts and current import task; preserve file switching
  on mobile; improve queue, password, error, duplicate, and confirmation states.
- **Transactions:** add a responsive filter/action bar and a mobile row layout while keeping
  all existing update behavior intact.
- **People, Reimbursements, Trips:** unify entity cards, selected/detail behavior, creation
  disclosure, empty states, and responsive totals.
- **Reconciliation:** turn the form/result pair into a guided comparison with clear input,
  processing, balanced, difference, and error states without changing calculations.
- **Settings:** separate appearance from classification management; use an accessible
  disclosure or sheet for rule creation, add Cancel and feedback, and confirm deletion.

## Implementation boundary after approval

Allowed:

- Frontend components, route markup, styles, accessibility attributes, frontend-only UI
  state, and frontend tests.
- Copy changes that clarify state or recovery without changing product rules.
- Reusing current API responses exactly as they exist.

Not allowed:

- Backend routes, parsers, services, models, migrations, calculations, authentication rules,
  database schema, or persistence behavior.
- New search, notification, or financial capabilities disguised as visual work.

## Verification plan after implementation

- Test 390 × 844, 768 × 1024, 1024 × 768, and 1440 × 900 layouts.
- Exercise every route plus login/register, theme selection, disclosed forms, filters,
  import queue/review controls, loading, empty, authentication, failure, and success states.
- Verify keyboard-only navigation, logical focus order, dialog focus containment/restoration,
  skip link, visible focus, Escape behavior, and route landmarks.
- Check text scaling, wrapping, overflow, table/card transitions, touch targets, contrast,
  status labels, live-region announcements, and reduced motion.
- Run ESLint, Vitest, the production build, and the existing browser smoke test; add focused
  frontend tests for shell responsiveness and shared states.

## Approval decision

Recommended: approve **Ledger Desk** as the implementation direction and apply it across all
current frontend routes under the boundary above.
