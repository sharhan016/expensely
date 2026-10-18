"use client";

import Link from "next/link";
import { AlertTriangle, Inbox, LoaderCircle, LogIn } from "lucide-react";

export function WorkspaceState({ loading, error, empty, emptyDetail, onRetry }: { loading?: boolean; error?: string; empty?: string; emptyDetail?: string; onRetry?: () => void }) {
  if (loading) return <section className="state-card loading-state" aria-live="polite" aria-busy="true"><LoaderCircle aria-hidden="true" /><div><strong>Loading workspace</strong><span>Fetching the latest financial records…</span></div></section>;
  if (error === "AUTH_REQUIRED") return <section className="state-card" role="alert"><LogIn aria-hidden="true" /><div><strong>Sign in required</strong><span>Your private workspace is available after authentication.</span></div><Link className="primary" href="/login">Sign in</Link></section>;
  if (error) return <section className="state-card error" role="alert"><AlertTriangle aria-hidden="true" /><div><strong>Couldn’t load this view</strong><span>{error}. Check the connection and try again.</span></div>{onRetry && <button className="secondary" onClick={onRetry}>Try again</button>}</section>;
  if (empty) return <section className="state-card empty-state"><Inbox aria-hidden="true" /><div><strong>{empty}</strong><span>{emptyDetail ?? "Import a statement or add a record to get started."}</span></div></section>;
  return null;
}
