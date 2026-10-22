"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CoverageReport } from "@/lib/coverage";

export function CoverageBanner() {
  const [report, setReport] = useState<CoverageReport | null>(null);
  useEffect(() => { api<CoverageReport>("/coverage").then(setReport).catch(() => setReport(null)); }, []);
  if (!report || !report.accounts.length) return null;
  return (
    <p className="coverage-banner" role="status" data-testid="coverage-banner">
      <span>{report.overall.banner}</span> <Link href="/records">See my records →</Link>
    </p>
  );
}
