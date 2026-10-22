"use client";

import { useState } from "react";
import { api, inr } from "@/lib/api";

export type Claim = { kind: string; text: string; evidence: string[] };
export type Investigation = { claims: Claim[]; evidence: { ref: string; label: string; type: string }[]; question: string | null };
type Opened = { transactions: { id: number; txn_date: string; account_name: string; description: string; amount: string }[] };

const KIND_LABEL: Record<string, string> = {
  exact: "Exact match", possible: "Possible, not a cause", close: "Close, not exact", fact: "Fact about the records",
  gap: "Gap in the records", none: "Nothing found", error: "A check failed", note: "", question: "One question",
};

// Lists what the assistant found. Every statement that claims something has records behind it,
// and "Open the records" shows those exact rows.
export function InvestigationCard({ context }: { context: Investigation }) {
  const [opened, setOpened] = useState<{ index: number; rows: Opened["transactions"] } | null>(null);
  const [error, setError] = useState("");
  async function open(index: number, refs: string[]) {
    setError("");
    try {
      const result = await api<Opened>("/investigate/evidence", { method: "POST", body: JSON.stringify({ refs }) });
      setOpened({ index, rows: result.transactions });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The records could not be opened"); }
  }
  return (
    <div className="investigation-card" role="group" aria-label="What I found">
      <ul>
        {context.claims.map((claim, index) => (
          <li key={index} className={`claim claim-${claim.kind}`}>
            {KIND_LABEL[claim.kind] && <small>{KIND_LABEL[claim.kind]}</small>}
            <span>{claim.text}</span>
            {claim.evidence.length > 0 && (
              <button className="text-button" onClick={() => open(index, claim.evidence)}>
                Open the {claim.evidence.filter(ref => ref.startsWith("txn:")).length || claim.evidence.length} record{claim.evidence.length === 1 ? "" : "s"}
              </button>
            )}
            {opened?.index === index && opened.rows.length > 0 && (
              <div className="table-scroll"><table className="responsive-table" aria-label="Evidence rows"><thead><tr><th>Date</th><th>Account</th><th>Description</th><th>Amount</th></tr></thead><tbody>
                {opened.rows.map(row => <tr key={row.id}><td data-label="Date">{row.txn_date}</td><td data-label="Account">{row.account_name}</td><td data-label="Description">{row.description}</td><td data-label="Amount">{inr(row.amount)}</td></tr>)}
              </tbody></table></div>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="records-error" role="alert">{error}</p>}
    </div>
  );
}
