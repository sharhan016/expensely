"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Bot } from "lucide-react";
import { api } from "@/lib/api";

type RunEvent = { seq: number; stage: string; message: string; status: string };
type Run = { id: string; updated_at?: string | null; imports_confirmed?: boolean; status: string; waiting_for: { type: string; import_id?: string; account_id?: number; gap_start?: string; gap_end?: string } | null; question: string | null; events: RunEvent[] };
type State = { latest_run: Run | null; review_period: { period_start: string; period_end: string } | null; greeting: string; run: Run | null };

async function fetchState(): Promise<State> {
  let current = await api<State>("/workflow/state");
  if (!current.review_period && !current.run) {
    const started = await api<Run>("/workflow/runs", { method: "POST", body: JSON.stringify({ import_ids: [], client_key: "welcome" }) });
    current = { ...current, run: started.status === "waiting" ? started : null, latest_run: started };
  }
  return current;
}

// The assistant's current turn in the conversation: its opening line, the one question it is
// waiting on, or what it did with the last answer. All wording and every decision comes from the
// backend; this component only collects the answer. `turn` counts messages sent this visit, so
// the opening line and the last note step aside once the conversation moves on.
export function WorkflowPanel({ refreshKey, turn, onDone }: { refreshKey: number; turn: number; onDone?: () => void }) {
  const [state, setState] = useState<State | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [noted, setNoted] = useState<{ turn: number; items: string[] }>({ turn: 0, items: [] });
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [password, setPassword] = useState("");

  const show = useCallback((current: State) => {
    setState(current); setRun(current.run);
    if (current.run?.status === "waiting") setNoted(previous => ({ ...previous, items: [] }));
  }, []);
  useEffect(() => {
    fetchState().then(show).catch(reason => setError(reason instanceof Error ? reason.message : "The assistant could not load"));
  }, [show, refreshKey]);

  async function send(path: string, body: object) {
    if (!run) return;
    setBusy(true);
    setError("");
    const seen = Math.max(0, ...run.events.map(item => item.seq));
    try {
      const next = await api<Run>(`/workflow/runs/${run.id}/${path}`, { method: "POST", body: JSON.stringify(body) });
      setRun(next);
      setPassword("");
      if (next.status !== "waiting") { show(await fetchState()); setNoted({ turn, items: next.events.filter(item => item.seq > seen).map(item => item.message) }); onDone?.(); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That answer could not be saved");
    } finally {
      setBusy(false);
    }
  }

  const waiting = run?.status === "waiting" ? run.waiting_for : null;
  const submitPeriod = (event: FormEvent) => { event.preventDefault(); send("answer", { answer: "period", period_start: first, period_end: last }); };
  const answerGap = (choice: string) => waiting && send("answer", { answer: "gap", choice, account_id: waiting.account_id, gap_start: waiting.gap_start, gap_end: waiting.gap_end });
  const submitPassword = (event: FormEvent) => { event.preventDefault(); if (waiting?.import_id) send("password", { import_id: waiting.import_id, password }); };

  if (!state) return error ? <p className="records-error" role="alert">{error}</p> : null;
  const greeting = turn === 0 ? state.greeting : "";
  const notes = noted.turn === turn ? noted.items : [];
  if (!greeting && !notes.length && !(waiting && run?.question) && !error) return null;
  return (
    <article className="assistant-message assistant workflow-turn">
      <span><Bot aria-hidden="true" /></span>
      <section className="workflow-panel" aria-label="Assistant progress">
      {greeting && <p data-testid="greeting">{greeting}</p>}
      {notes.map((item, index) => <p key={index} role="status">{item}</p>)}
      {waiting && run?.question && (
        <div className="workflow-question" role="group" aria-label="The assistant needs an answer">
          <strong data-testid="question">{run.question}</strong>
          {waiting.type === "period" && (
            <form onSubmit={submitPeriod}>
              <label>First date<input type="date" required value={first} onChange={e => setFirst(e.target.value)} /></label>
              <label>Last date<input type="date" required value={last} onChange={e => setLast(e.target.value)} /></label>
              <button className="primary" type="submit" disabled={busy}>Save period</button>
            </form>
          )}
          {waiting.type === "gap" && (
            <div className="workflow-actions">
              <button className="secondary" disabled={busy} onClick={() => answerGap("will_upload")}>I’ll upload that statement</button>
              <button className="primary" disabled={busy} onClick={() => answerGap("not_needed")}>That period is not needed</button>
            </div>
          )}
          {waiting.type === "password" && (
            <form onSubmit={submitPassword}>
              <label>PDF password<input type="password" autoComplete="off" required value={password} onChange={e => setPassword(e.target.value)} /></label>
              <button className="primary" type="submit" disabled={busy}>Unlock and continue</button>
            </form>
          )}
        </div>
      )}
      {error && <p className="records-error" role="alert">{error}</p>}
      </section>
    </article>
  );
}
