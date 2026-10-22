import type { Browser } from "@playwright/test";

// Synthetic statements only. Money is integer paise throughout; nothing here is real data.
export type SyntheticRow = { date: string; narration: string; debit?: number; credit?: number };
export type StatementRow = SyntheticRow & { balance: number };

export function paise(value: number) {
  return `${Math.trunc(value / 100)}.${String(Math.abs(value % 100)).padStart(2, "0")}`;
}

export function inrText(value: number) {
  return (value / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function buildStatement(opening: number, rows: SyntheticRow[]) {
  let balance = opening;
  const withBalances: StatementRow[] = rows.map(row => {
    balance += (row.credit ?? 0) - (row.debit ?? 0);
    return { ...row, balance };
  });
  const out = rows.reduce((sum, row) => sum + (row.debit ?? 0), 0);
  const inflow = rows.reduce((sum, row) => sum + (row.credit ?? 0), 0);
  return { opening, closing: balance, rows: withBalances, out, in: inflow };
}

export function statementHtml(rows: StatementRow[], title = "HDFC Bank Statement of Account") {
  const body = rows
    .map(
      row =>
        `<tr><td>${row.date}</td><td>${row.narration}</td><td>${row.debit ? paise(row.debit) : ""}</td>` +
        `<td>${row.credit ? paise(row.credit) : ""}</td><td>${paise(row.balance)}</td></tr>`,
    )
    .join("");
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    body{font-family:Arial,sans-serif;font-size:12px;margin:24px}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #000;padding:6px;text-align:left}
  </style></head><body><h1>${title}</h1><p>Synthetic test statement</p>
  <table><thead><tr><th>Date</th><th>Narration</th><th>Withdrawal</th><th>Deposit</th><th>Closing Balance</th></tr></thead>
  <tbody>${body}</tbody></table></body></html>`;
}

export async function renderPdf(browser: Browser, html: string): Promise<Buffer> {
  const page = await browser.newPage();
  await page.setContent(html);
  const pdf = await page.pdf({ format: "A4" });
  await page.close();
  return pdf;
}
