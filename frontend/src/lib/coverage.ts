// Display helpers for the coverage API. All dates, balances and verdicts are decided by the
// backend; nothing here computes money or decides what is reconciled.
import { dateLabel } from "./ledger";

export type Segment = { kind: string; start: string; end: string; days: number; detail: string; statement_id: string | null; unbridged: boolean };
export type AccountCoverage = {
  account_id: number; name: string; instrument_type: string; in_scope: boolean;
  records_start: string | null; records_start_is_default: boolean; records_opening_balance: string | null;
  reconciled_through: string | null; covered: [string, string][]; segments: Segment[];
  first_problem: Segment | null; summary: string;
};
export type CoverageReport = {
  as_of_today: string;
  overall: { reconciled_through: string | null; lagging_account_id: number | null; lagging_name: string | null; in_scope_count: number; summary: string; banner: string };
  accounts: AccountCoverage[];
};

export const KIND_LABELS: Record<string, string> = {
  covered: "Covered and adds up",
  bridged: "Bridged: balances connect, but no statement rows for these dates",
  gap: "Not covered",
  discontinuity: "Balances do not connect",
  difference: "Statement does not add up",
  unverifiable: "Covered, but cannot be checked",
  unverified: "Needs a re-check",
  invalidated: "Changed after it was checked",
  period_mismatch: "Statement adds up, but other movements in these dates do not",
};

export function rangeLabel(start: string, end: string) {
  return start === end ? dateLabel(start) : `${dateLabel(start)} to ${dateLabel(end)}`;
}

/** Widths for a coverage bar: proportional to days, never thinner than `floor` percent. */
export function segmentPercents(segments: { days: number }[], floor = 3): number[] {
  const total = segments.reduce((sum, item) => sum + item.days, 0);
  if (!segments.length || total <= 0) return [];
  const raw = segments.map(item => (item.days / total) * 100);
  const small = raw.filter(value => value < floor).length;
  const bigTotal = raw.filter(value => value >= floor).reduce((sum, value) => sum + value, 0);
  const scale = bigTotal > 0 ? (100 - small * floor) / bigTotal : 0;
  return raw.map(value => (value < floor ? floor : value * scale));
}
