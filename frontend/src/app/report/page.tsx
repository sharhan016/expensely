"use client";

import { FormEvent, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { api, inr } from "@/lib/api";
import { dateLabel } from "@/lib/ledger";

type Bucket = { count: number; out: string; in: string };
type Report = {
  start: string; end: string; movement_count: number; empty: boolean; summary: string; cash_note: string;
  buckets: Record<string, Bucket>; labels: Record<string, string>;
  days: { date: string; count: number; out: string; in: string }[];
  counterparties: { name: string; confirmed: boolean; count: number; out: string }[];
  coverage: { status: string; notes: string[] };
};
type Row = { id: number; txn_date: string; account_name: string; description: string; amount: string; bucket: string };

const isoDay = (offsetDays: number) => {
  const day = new Date();
  day.setDate(day.getDate() + offsetDays);
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
};

export default function ReportPage() {
  const [accent, setAccent] = useState(() => (typeof window === "undefined" ? "cobalt" : localStorage.getItem("expense-accent") ?? "cobalt"));
  const [from, setFrom] = useState(isoDay(-30));
  const [to, setTo] = useState(isoDay(0));
  const [report, setReport] = useState<Report | null>(null);
  const [rows, setRows] = useState<{ title: string; items: Row[] } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Start from her saved review period when she has one.
    api<{ period_start: string; period_end: string } | null>("/review-period")
      .then(period => { if (period) { setFrom(period.period_start); setTo(period.period_end); } })
      .catch(() => undefined);
  }, []);

  const query = `from=${from}&to=${to}`;
  async function run(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true); setError(""); setNotice(""); setRows(null);
    try { setReport(await api<Report>(`/reports/period?${query}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The report could not be built"); }
    finally { setBusy(false); }
  }
  async function drill(title: string, extra: string) {
    try { setRows({ title, items: await api<Row[]>(`/reports/period/rows?${query}${extra}`) }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Rows could not be loaded"); }
  }
  async function copyTable() {
    setError("");
    try {
      const response = await fetch(`/api/reports/period.tsv?${query}`, { credentials: "include" });
      if (!response.ok) throw new Error("The table could not be built");
      const text = await response.text();
      await navigator.clipboard.writeText(text);
      setNotice(`Copied ${text.trim().split("\n").length - 1} rows. Paste them into Excel.`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Copy failed"); }
  }

  return (
    <AppShell accent={accent} onAccent={setAccent} active="Report">
      <header className="page-head">
        <div><p className="kicker">Understand a period</p><h1>Period report</h1><p>What moved between two dates, split so nothing is called spending by guess.</p></div>
      </header>
      <form className="report-form" onSubmit={run}>
        <label>From<input type="date" required value={from} onChange={e => setFrom(e.target.value)} /></label>
        <label>To<input type="date" required value={to} onChange={e => setTo(e.target.value)} /></label>
        <button className="primary" type="submit" disabled={busy}>Show report</button>
      </form>
      {error && <p className="records-error" role="alert">{error}</p>}
      {report && (
        <section className="report-body" aria-label="Report">
          <p className="report-summary" role="status" data-testid="report-summary">{report.summary}</p>
          {report.cash_note && <p className="records-note">{report.cash_note}</p>}
          {report.coverage.notes.length > 0 && (
            <ul className={`report-coverage ${report.coverage.status}`} aria-label="What is not covered">
              {report.coverage.notes.map(note => <li key={note}>{note}</li>)}
            </ul>
          )}
          <div className="report-actions">
            <button className="secondary" onClick={copyTable} disabled={report.empty}>Copy as table</button>
            <a className="secondary" href={`/api/reports/period.csv?${query}`} download>Download CSV</a>
            <button className="secondary" onClick={() => window.print()}>Print</button>
          </div>
          {notice && <p className="records-notice" role="status">{notice}</p>}
          {!report.empty && (
            <>
              <h2>Totals</h2>
              <div className="table-scroll"><table className="responsive-table"><thead><tr><th>Group</th><th>Movements</th><th>Out</th><th>In</th></tr></thead><tbody>
                {Object.entries(report.buckets).filter(([, b]) => b.count > 0).map(([name, b]) => (
                  <tr key={name}>
                    <td data-label="Group"><button className="text-button" onClick={() => drill(report.labels[name], `&bucket=${name}`)}>{report.labels[name]}</button></td>
                    <td data-label="Movements">{b.count}</td><td data-label="Out">{inr(b.out)}</td><td data-label="In">{inr(b.in)}</td>
                  </tr>
                ))}
              </tbody></table></div>
              <h2>Day by day</h2>
              <div className="table-scroll"><table className="responsive-table"><thead><tr><th>Date</th><th>Movements</th><th>Out</th><th>In</th></tr></thead><tbody>
                {report.days.map(day => (
                  <tr key={day.date}>
                    <td data-label="Date"><button className="text-button" onClick={() => drill(dateLabel(day.date), `&date=${day.date}`)}>{dateLabel(day.date)}</button></td>
                    <td data-label="Movements">{day.count}</td><td data-label="Out">{inr(day.out)}</td><td data-label="In">{inr(day.in)}</td>
                  </tr>
                ))}
              </tbody></table></div>
            </>
          )}
          {rows && (
            <>
              <h2 data-testid="drill-title">Rows: {rows.title}</h2>
              <div className="table-scroll"><table className="responsive-table" aria-label="Rows"><thead><tr><th>Date</th><th>Account</th><th>Description</th><th>Amount</th></tr></thead><tbody>
                {rows.items.map(item => (
                  <tr key={item.id}><td data-label="Date">{item.txn_date}</td><td data-label="Account">{item.account_name}</td><td data-label="Description">{item.description}</td><td data-label="Amount">{inr(item.amount)}</td></tr>
                ))}
              </tbody></table></div>
            </>
          )}
        </section>
      )}
    </AppShell>
  );
}
