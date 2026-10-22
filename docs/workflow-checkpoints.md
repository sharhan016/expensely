# Workflow checkpoints (LangGraph)

Our `workflow_runs` and `workflow_events` tables are the source of truth for what the assistant
is doing, what it is waiting for, and what she has been told. LangGraph checkpoints exist only so
a paused graph can resume after a restart. They hold **ids, dates and enum codes only**: never
descriptions, names, amounts, account numbers or passwords.

## Why checkpoint tables are not in Alembic

`PostgresSaver.setup()` creates and versions its own tables (`checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, `checkpoint_migrations`). Alembic does not manage them and `alembic
downgrade` leaves them alone. They contain no financial data, so leaving them is harmless.

## Where `setup()` runs

- The workflow service calls it the first time a PostgreSQL session is used in a process (cached per URL). It is idempotent.
- CI: `make verify` migrates, then runs the integration and end-to-end suites; the first workflow
  run calls `setup()`. No extra CI step is needed.
- SQLite (the default unit-test database) uses an in-memory checkpointer; durable resume is
  proven on PostgreSQL only (`tests/integration/test_langgraph_spike.py` with separate processes,
  and the Playwright journey, which restarts the real server through `backend/scripts/e2e_supervisor.py`).

## Rules for graph code

1. Checkpointed state and `interrupt()` payloads carry ids, dates and codes only.
2. Never pass a password through `interrupt()` or `Command(resume=...)`: resume values are
   persisted. The password is used inside the request that receives it, and the graph is resumed
   with a code such as `{"answered": "password"}`.
3. A node re-runs from its start when resumed, so side effects before an `interrupt()` must be
   idempotent. Events use a unique `(run_id, step_key)`.
4. Put non-idempotent work in its own node ahead of the interrupting node.
