"use client";

import { useCallback, useEffect, useState } from "react";
import { api, inr } from "@/lib/api";
import { dateLabel } from "@/lib/ledger";

type Question = { counterparty_id: number; counterparty_name: string; count: number; total: string; first_date: string; last_date: string };
type Outstanding = { expectation_id: number; counterparty_name: string; sent: string; returned: string; remaining: string; status: string; since: string; age_days: number };
type ReturnProposal = { expectation_id: number; transaction_id: number; counterparty_name: string; date: string; amount: string; remaining: string };
type TransferProposal = { outflow_id: number; inflow_id: number; amount: string; out_date: string; in_date: string };
type Motion = { questions: Question[]; outstanding: Outstanding[]; return_proposals: ReturnProposal[]; transfer_proposals: TransferProposal[] };

// Money she sent out. Nothing is assumed: each person is asked about once, and only her answer
// changes how the payments are counted.
export function MotionPanel({ refreshKey }: { refreshKey: number }) {
  const [state, setState] = useState<Motion | null>(null);
  const [later, setLater] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api<Motion>("/motion").then(setState).catch(() => undefined); }, [refreshKey]);
  const post = useCallback(async (path: string, body?: object) => {
    setBusy(true); setError("");
    try { setState(await api<Motion>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "That could not be saved"); }
    finally { setBusy(false); }
  }, []);
  const hide = (key: string) => setLater(current => new Set(current).add(key));

  if (!state) return null;
  const questions = state.questions.filter(item => !later.has(`q${item.counterparty_id}`));
  const proposals = state.return_proposals.filter(item => !later.has(`r${item.transaction_id}`));
  const transfers = state.transfer_proposals.filter(item => !later.has(`t${item.outflow_id}`));
  if (!questions.length && !proposals.length && !transfers.length && !state.outstanding.length) return null;

  return (
    <section className="workflow-panel motion-panel" aria-label="Money you sent out">
      {questions.map(item => (
        <div className="workflow-question" role="group" aria-label={`Question about ${item.counterparty_name}`} key={item.counterparty_id}>
          <strong>You sent {item.count} payment{item.count === 1 ? "" : "s"} totalling {inr(item.total)} to {item.counterparty_name} ({dateLabel(item.first_date)}{item.first_date === item.last_date ? "" : ` to ${dateLabel(item.last_date)}`}). Do you expect it back?</strong>
          <div className="workflow-actions">
            <button className="primary" disabled={busy} onClick={() => post("/motion/answers", { counterparty_id: item.counterparty_id, answer: "expected_back" })}>I expect it back</button>
            <button className="secondary" disabled={busy} onClick={() => post("/motion/answers", { counterparty_id: item.counterparty_id, answer: "expense" })}>It was an expense</button>
            <button className="text-button" onClick={() => hide(`q${item.counterparty_id}`)}>Ask me later</button>
          </div>
        </div>
      ))}
      {proposals.map(item => (
        <div className="workflow-question" role="group" aria-label={`Possible return from ${item.counterparty_name}`} key={item.transaction_id}>
          <strong>{inr(item.amount)} came in from {item.counterparty_name} on {dateLabel(item.date)}. Is it a return of what you sent? {inr(item.remaining)} is still expected back.</strong>
          <div className="workflow-actions">
            <button className="primary" disabled={busy} onClick={() => post("/motion/returns", { expectation_id: item.expectation_id, transaction_id: item.transaction_id })}>Yes, it came back</button>
            <button className="secondary" disabled={busy} onClick={() => post("/motion/returns/dismiss", { expectation_id: item.expectation_id, transaction_id: item.transaction_id })}>No, it is something else</button>
          </div>
        </div>
      ))}
      {transfers.map(item => (
        <div className="workflow-question" role="group" aria-label="Possible transfer between your accounts" key={item.outflow_id}>
          <strong>{inr(item.amount)} left one of your accounts on {dateLabel(item.out_date)} and {inr(item.amount)} arrived in another on {dateLabel(item.in_date)}. Is that a transfer between your own accounts?</strong>
          <div className="workflow-actions">
            <button className="primary" disabled={busy} onClick={() => post("/motion/transfers", { outflow_id: item.outflow_id, inflow_id: item.inflow_id })}>Yes, my own accounts</button>
            <button className="text-button" onClick={() => hide(`t${item.outflow_id}`)}>Not now</button>
          </div>
        </div>
      ))}
      {state.outstanding.length > 0 && (
        <ul className="motion-outstanding" aria-label="Still out">
          {state.outstanding.map(item => (
            <li key={item.expectation_id}>
              <span><strong>{item.counterparty_name}</strong>: {inr(item.remaining)} still out of {inr(item.sent)} since {dateLabel(item.since)} ({item.age_days} days){Number(item.returned) > 0 ? `, ${inr(item.returned)} has come back` : ""} · {item.status.replace("_", " ")}</span>
              {item.status === "open" || item.status === "partly_settled" ? (
                <button className="text-button" disabled={busy} onClick={() => post(`/motion/expectations/${item.expectation_id}/status`, { status: "not_expected" })}>Not expected back after all</button>
              ) : (
                <button className="text-button" disabled={busy} onClick={() => post(`/motion/expectations/${item.expectation_id}/status`, { status: "open" })}>Track it again</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="records-error" role="alert">{error}</p>}
    </section>
  );
}
