"use client";

import { FormEvent, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { WorkspaceState } from "@/components/WorkspaceState";
import { api, inr } from "@/lib/api";
import { dateLabel, showingLabel } from "@/lib/ledger";
import { AccountCoverage, CoverageReport, KIND_LABELS, rangeLabel, segmentPercents } from "@/lib/coverage";

type Cash = {
  withdrawn_total: string; withdrawal_count: number; since: string | null; explained_by_you: string; not_yet_explained: string;
  summary: string; note: string;
  withdrawals: { transaction_id: number; txn_date: string; description: string; amount: string; source: string }[];
  entries: { id: number; entry_date: string; amount: string; note: string; label: string }[];
};

export default function Records() {
  const [accent, setAccent] = useState(() => (typeof window === "undefined" ? "cobalt" : localStorage.getItem("expense-accent") ?? "cobalt"));
  const [report, setReport] = useState<CoverageReport | null>(null);
  const [cash, setCash] = useState<Cash | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    Promise.all([api<CoverageReport>("/coverage"), api<Cash>("/cash")])
      .then(([coverage, cashSummary]) => { setReport(coverage); setCash(cashSummary); })
      .catch(reason => setError(reason instanceof Error ? reason.message : "Records could not be loaded"))
      .finally(() => setLoading(false));
  }, []);

  async function act<T>(work: () => Promise<T>, done?: (value: T) => void) {
    setBusy(true);
    setError("");
    try {
      const value = await work();
      done?.(value);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That could not be saved");
    } finally {
      setBusy(false);
    }
  }

  const recheck = () => act(() => api<{ rechecked: number; coverage: CoverageReport }>("/coverage/recompute", { method: "POST" }), result => {
    setReport(result.coverage);
    setNotice(`Re-checked ${result.rechecked} statement${result.rechecked === 1 ? "" : "s"} against your records.`);
  });

  return (
    <AppShell accent={accent} onAccent={setAccent} active="Records">
      <header className="page-head">
        <div>
          <p className="kicker">What my records cover</p>
          <h1>Records and coverage</h1>
          <p>For each account, the dates covered by statements that add up, and what is missing.</p>
        </div>
        <button className="secondary" onClick={recheck} disabled={busy}>Re-check records</button>
      </header>
      <WorkspaceState loading={loading} error={error && !report ? error : ""} empty={!loading && !error && report && !report.accounts.length ? "No accounts yet." : undefined} />
      {error && report && <p className="records-error" role="alert">{error}</p>}
      {notice && <p className="records-notice" role="status">{notice}</p>}
      {report && report.accounts.length > 0 && (
        <>
          <section className="records-overall" aria-label="Overall status">
            <p className="kicker">Overall</p>
            <h2 data-testid="overall-summary">{report.overall.summary}</h2>
            {report.overall.banner !== report.overall.summary && <p>{report.overall.banner}</p>}
          </section>
          <div className="records-grid">
            {report.accounts.map(account => (
              <AccountCard key={account.account_id} account={account} busy={busy}
                onChange={(work) => act(work, setReport)} />
            ))}
          </div>
        </>
      )}
      {cash && <CashCard cash={cash} busy={busy} onChange={(work) => act(work, setCash)} />}
    </AppShell>
  );
}

function AccountCard({ account, busy, onChange }: { account: AccountCoverage; busy: boolean; onChange: (work: () => Promise<CoverageReport>) => void }) {
  const widths = segmentPercents(account.segments);
  const [start, setStart] = useState(account.records_start_is_default ? "" : account.records_start ?? "");
  const [opening, setOpening] = useState(account.records_opening_balance ?? "");
  const patch = (body: object) => onChange(() => api<CoverageReport>(`/coverage/accounts/${account.account_id}`, { method: "PATCH", body: JSON.stringify(body) }));
  const gapBody = (segment: { start: string; end: string }) => ({ gap_start: segment.start, gap_end: segment.end });
  function saveStart(event: FormEvent) {
    event.preventDefault();
    patch(start ? { records_start: start, records_opening_balance: opening || null } : { records_start: null });
  }
  return (
    <article className={`records-card ${account.in_scope ? "" : "out"}`} aria-label={`Coverage for ${account.name}`}>
      <header>
        <div>
          <h3>{account.name}</h3>
          <p className="records-through">
            {account.reconciled_through ? <>Reconciled through <strong>{dateLabel(account.reconciled_through)}</strong></> : "Nothing reconciled yet"}
          </p>
        </div>
        <label className="records-scope">
          <input type="checkbox" checked={account.in_scope} disabled={busy} onChange={event => patch({ in_scope: event.target.checked })} />
          Include in the overall line
        </label>
      </header>
      <p className="records-summary">{account.summary}</p>
      {widths.length > 0 && (
        <div className="coverage-bar" role="img" aria-label={`Coverage bar for ${account.name}: ${account.segments.map(s => `${KIND_LABELS[s.kind] ?? s.kind} ${rangeLabel(s.start, s.end)}`).join("; ")}`}>
          {account.segments.map((segment, index) => (
            <i key={`${segment.start}-${segment.kind}-${index}`} className={`seg seg-${segment.kind}`} style={{ width: `${widths[index]}%` }} title={`${KIND_LABELS[segment.kind] ?? segment.kind}: ${rangeLabel(segment.start, segment.end)}`} />
          ))}
        </div>
      )}
      <ul className="records-segments">
        {account.segments.map((segment, index) => (
          <li key={`${segment.start}-${segment.kind}-${index}`} className={`seg-${segment.kind}`}>
            <span className="seg-dot" aria-hidden="true" />
            <div>
              <strong>{KIND_LABELS[segment.kind] ?? segment.kind}</strong>
              <span>{rangeLabel(segment.start, segment.end)} · {segment.days} day{segment.days === 1 ? "" : "s"}</span>
              {segment.detail && <em>{segment.detail}</em>}
            </div>
            {segment.kind === "bridged" && <button className="text-button" disabled={busy} onClick={() => onChange(() => api<CoverageReport>(`/coverage/accounts/${account.account_id}/unbridged-gaps`, { method: "POST", body: JSON.stringify(gapBody(segment)) }))}>Un-bridge</button>}
            {segment.kind === "gap" && segment.unbridged && <button className="text-button" disabled={busy} onClick={() => onChange(async () => { await api(`/coverage/accounts/${account.account_id}/unbridged-gaps?gap_start=${segment.start}&gap_end=${segment.end}`, { method: "DELETE" }); return api<CoverageReport>("/coverage"); })}>Bridge again</button>}
          </li>
        ))}
        {!account.segments.length && <li><div><strong>No confirmed statements yet</strong><span>Upload a statement to start your records.</span></div></li>}
      </ul>
      <form className="records-start" onSubmit={saveStart}>
        <fieldset disabled={busy}>
          <legend>Where my records begin{account.records_start_is_default ? " (default: the first statement)" : ""}</legend>
          <label>Start date<input type="date" value={start} onChange={event => setStart(event.target.value)} /></label>
          <label>Opening balance I confirm (optional)<input inputMode="decimal" value={opening} placeholder="e.g. 1000.00" onChange={event => setOpening(event.target.value)} /></label>
          <button className="secondary" type="submit">{start ? "Save start" : "Use the first statement"}</button>
        </fieldset>
      </form>
    </article>
  );
}

function CashCard({ cash, busy, onChange }: { cash: Cash; busy: boolean; onChange: (work: () => Promise<Cash>) => void }) {
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  function add(event: FormEvent) {
    event.preventDefault();
    onChange(() => api<Cash>("/cash/entries", { method: "POST", body: JSON.stringify({ entry_date: date, amount, note }) }));
    setAmount(""); setNote("");
  }
  return (
    <section className="records-cash" aria-label="Cash in hand">
      <p className="kicker">Cash in hand</p>
      <h2 data-testid="cash-summary">{cash.summary}</h2>
      <p className="records-note">{cash.note}</p>
      {cash.withdrawals.length > 0 && (
        <>
          <p className="records-count">{showingLabel(cash.withdrawals.length, cash.withdrawal_count)} withdrawals</p>
          <ul className="records-withdrawals">
            {cash.withdrawals.map(item => (
              <li key={item.transaction_id}>
                <div><strong>{inr(item.amount)}</strong><span>{dateLabel(item.txn_date)} · {item.description}</span></div>
                <button className="text-button" disabled={busy} onClick={() => onChange(() => api<Cash>(`/cash/withdrawals/${item.transaction_id}`, { method: "PUT", body: JSON.stringify({ is_cash_withdrawal: false }) }))}>Not a cash withdrawal</button>
              </li>
            ))}
          </ul>
        </>
      )}
      <form className="records-cash-form" onSubmit={add}>
        <fieldset disabled={busy}>
          <legend>Cash I spent (my own record, no statement behind it)</legend>
          <label>Date<input type="date" required value={date} onChange={event => setDate(event.target.value)} /></label>
          <label>Amount<input inputMode="decimal" required value={amount} onChange={event => setAmount(event.target.value)} /></label>
          <label>Note<input value={note} maxLength={200} onChange={event => setNote(event.target.value)} /></label>
          <button className="secondary" type="submit">Add</button>
        </fieldset>
      </form>
      {cash.entries.length > 0 && (
        <ul className="records-withdrawals">
          {cash.entries.map(item => (
            <li key={item.id}>
              <div><strong>{inr(item.amount)}</strong><span>{dateLabel(item.entry_date)} · {item.label}{item.note ? ` · ${item.note}` : ""}</span></div>
              <button className="text-button" disabled={busy} onClick={() => onChange(async () => { await api(`/cash/entries/${item.id}`, { method: "DELETE" }); return api<Cash>("/cash"); })}>Remove</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
