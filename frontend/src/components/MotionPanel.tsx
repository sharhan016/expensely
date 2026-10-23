"use client";

import { useCallback, useEffect, useState } from "react";
import { api, inr } from "@/lib/api";
import { dateLabel } from "@/lib/ledger";

type Question = { remembered: string | null; counterparty_id: number; counterparty_name: string; count: number; total: string; first_date: string; last_date: string };
type Outstanding = { expectation_id: number; counterparty_name: string; sent: string; returned: string; remaining: string; status: string; since: string; age_days: number; from_loans: boolean };
type Remembered = { id: number; counterparty_name: string; choice: string; applied_count: number };
type LoanProposal = { person_id: number; person_name: string; loans: { count: number; total: string }; repayments: { count: number; total: string }; loan_balance_now: string; would_track: { sent: string; returned: string } };
type ReturnProposal = { expectation_id: number; transaction_id: number; counterparty_name: string; date: string; amount: string; remaining: string };
type TransferProposal = { outflow_id: number; inflow_id: number; amount: string; out_date: string; in_date: string };
type PassThrough = { outflow_id: number; inflow_id: number; amount: string; in_date: string; out_date: string; from_name: string; to_name: string };
type Motion = { pass_through_proposals?: PassThrough[]; remembered: Remembered[]; loan_proposals: LoanProposal[]; questions: Question[]; outstanding: Outstanding[]; return_proposals: ReturnProposal[]; transfer_proposals: TransferProposal[] };

// Money she sent out. Nothing is assumed: each person is asked about once, and only her answer
// changes how the payments are counted.
export function MotionPanel({ refreshKey }: { refreshKey: number }) {
  const [state, setState] = useState<Motion | null>(null);
  const [later, setLater] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [remember, setRemember] = useState<Set<number>>(new Set());
  const [skipped, setSkipped] = useState<number[]>([]);

  useEffect(() => { api<Motion>("/motion").then(setState).catch(() => undefined); }, [refreshKey]);
  const post = useCallback(async (path: string, body?: object, method = "POST") => {
    setBusy(true); setError("");
    try { setState(await api<Motion>(path, { method, body: body ? JSON.stringify(body) : undefined })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "That could not be saved"); }
    finally { setBusy(false); }
  }, []);
  const toggleRemember = (id: number) => setRemember(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const hide = (key: string) => setLater(current => new Set(current).add(key));

  if (!state) return null;
  const queue = state.questions.filter(item => !later.has(`q${item.counterparty_id}`)).sort((a, b) => skipped.indexOf(a.counterparty_id) - skipped.indexOf(b.counterparty_id));
  const questions = queue.slice(0, 1); // one person at a time, biggest first
  const proposals = state.return_proposals.filter(item => !later.has(`r${item.transaction_id}`));
  const loans = state.loan_proposals.filter(item => !later.has(`l${item.person_id}`));
  const passing = (state.pass_through_proposals ?? []).filter(item => !later.has(`p${item.outflow_id}`)).slice(0, 1); // largest first, one at a time
  const passingTotal = (state.pass_through_proposals ?? []).filter(item => !later.has(`p${item.outflow_id}`)).length;
  const transfers = state.transfer_proposals.filter(item => !later.has(`t${item.outflow_id}`));
  if (!questions.length && !passing.length && !proposals.length && !transfers.length && !loans.length && !state.outstanding.length && !state.remembered.length) return null;

  return (
    <section className="workflow-panel motion-panel" aria-label="Money you sent out">
      {passing.map(item => (
        <div className="workflow-question" role="group" aria-label="Money that may have passed through your account" key={item.outflow_id}>
          <strong>{inr(item.amount)} came in{item.from_name ? ` from ${item.from_name}` : ""} on {dateLabel(item.in_date)} and {inr(item.amount)} went out{item.to_name ? ` to ${item.to_name}` : ""} {item.in_date === item.out_date ? "the same day" : `on ${dateLabel(item.out_date)}`}. Did the money just pass through your account?</strong>
          <small>I cannot tell whose money it was from the statement. If you say yes, I will leave both out of your spending and income.{passingTotal > 1 ? ` ${passingTotal - 1} more like this after this one.` : ""}</small>
          <div className="workflow-actions">
            <button className="primary" disabled={busy} onClick={() => post("/motion/pass-through", { outflow_id: item.outflow_id, inflow_id: item.inflow_id })}>Yes, it passed through</button>
            <button className="secondary" disabled={busy} onClick={() => post("/motion/pass-through/reject", { outflow_id: item.outflow_id, inflow_id: item.inflow_id })}>No, they are unrelated</button>
            <button className="text-button" onClick={() => hide(`p${item.outflow_id}`)}>Ask me later</button>
          </div>
        </div>
      ))}
      {questions.map(item => (
        <div className="workflow-question" role="group" aria-label={`Question about ${item.counterparty_name}`} key={item.counterparty_id}>
          <strong>{item.remembered === "expected_back" ? `You usually expect money from ${item.counterparty_name} back. Track ` : "You sent "}{item.count} payment{item.count === 1 ? "" : "s"} totalling {inr(item.total)}{item.remembered === "expected_back" ? " as expected back?" : ` to ${item.counterparty_name} (${dateLabel(item.first_date)}${item.first_date === item.last_date ? "" : ` to ${dateLabel(item.last_date)}`}). Do you expect it back?`}</strong>
          {queue.length > 1 && <small>Question 1 of {queue.length}, largest payments first. {queue.length - 1} more after this one.</small>}
          <label className="motion-remember"><input type="checkbox" checked={remember.has(item.counterparty_id)} onChange={() => toggleRemember(item.counterparty_id)} /> Remember my answer for this person</label>
          <div className="workflow-actions">
            <button className="primary" disabled={busy} onClick={() => post("/motion/answers", { counterparty_id: item.counterparty_id, answer: "expected_back", remember: remember.has(item.counterparty_id) })}>{item.remembered === "expected_back" ? "Yes, track them" : "I expect it back"}</button>
            <button className="secondary" disabled={busy} onClick={() => post("/motion/answers", { counterparty_id: item.counterparty_id, answer: "expense", remember: remember.has(item.counterparty_id) })}>It was an expense</button>
            <button className="text-button" onClick={() => (queue.length > 1 ? setSkipped(current => [...current.filter(id => id !== item.counterparty_id), item.counterparty_id]) : hide(`q${item.counterparty_id}`))}>Ask me later</button>
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
      {loans.map(item => (
        <div className="workflow-question" role="group" aria-label={`Loans to ${item.person_name}`} key={item.person_id}>
          <strong>You recorded {item.loans.count} loan{item.loans.count === 1 ? "" : "s"} totalling {inr(item.loans.total)} to {item.person_name}{item.repayments.count ? ` and ${item.repayments.count} repayment${item.repayments.count === 1 ? "" : "s"} totalling ${inr(item.repayments.total)}` : ""}. Your loan balance for them is {inr(item.loan_balance_now)}. Track them as money expected back?</strong>
          <small>If you say yes, {inr(item.would_track.sent)} sent and {inr(item.would_track.returned)} returned will move out of the loans group into money sent out and expected back. Nothing changes until you say yes, and you can undo it.</small>
          <div className="workflow-actions">
            <button className="primary" disabled={busy} onClick={() => post("/motion/loans/accept", { person_id: item.person_id })}>Yes, track them</button>
            <button className="text-button" onClick={() => hide(`l${item.person_id}`)}>Not now</button>
          </div>
        </div>
      ))}
      {state.remembered.length > 0 && (
        <ul className="motion-outstanding" aria-label="Remembered choices">
          {state.remembered.map(item => (
            <li key={item.id}>
              <span>For <strong>{item.counterparty_name}</strong>: {item.choice === "expense" ? `payments are counted as expenses automatically (${item.applied_count} later payment${item.applied_count === 1 ? "" : "s"} so far)` : "I will ask you to confirm new payments as expected back"}.</span>
              <button className="text-button" disabled={busy} onClick={() => post(`/motion/preferences/${item.id}`, undefined, "DELETE")}>Stop remembering</button>
              {item.choice === "expense" && <button className="text-button" disabled={busy} onClick={() => post(`/motion/preferences/${item.id}/undo`)}>Undo applied and stop remembering</button>}
            </li>
          ))}
        </ul>
      )}
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
              <span>{item.status === "written_off"
                ? <><strong>Written off (not counted)</strong>: {inr(item.sent)} to {item.counterparty_name} since {dateLabel(item.since)}</>
                : <><strong>{item.counterparty_name}</strong>: {inr(item.remaining)} still out of {inr(item.sent)} since {dateLabel(item.since)} ({item.age_days} days){Number(item.returned) > 0 ? `, ${inr(item.returned)} has come back` : ""} · {item.status.replace("_", " ")}</>}
                {item.from_loans ? " · from your recorded loans" : ""}</span>
              {(item.status === "open" || item.status === "partly_settled") && <>
                <button className="text-button" disabled={busy} onClick={() => post(`/motion/expectations/${item.expectation_id}/status`, { status: "written_off" })}>Write off</button>
                <button className="text-button" disabled={busy} onClick={() => post(`/motion/expectations/${item.expectation_id}/status`, { status: "not_expected" })}>Not expected back after all</button>
              </>}
              {item.status === "written_off" && <button className="text-button" disabled={busy} onClick={() => post(`/motion/expectations/${item.expectation_id}/status`, { status: "open" })}>Undo write-off</button>}
              {item.status === "not_expected" && <button className="text-button" disabled={busy} onClick={() => post(`/motion/expectations/${item.expectation_id}/status`, { status: "open" })}>Track it again</button>}
              {item.from_loans && <button className="text-button" disabled={busy} onClick={() => post(`/motion/loans/${item.expectation_id}/revert`)}>Undo mapping</button>}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="records-error" role="alert">{error}</p>}
    </section>
  );
}
