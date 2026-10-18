"use client";

import Link from "next/link";

export function WorkspaceState({loading,error,empty,emptyDetail}:{loading?:boolean;error?:string;empty?:string;emptyDetail?:string}){
  if(loading)return <div className="state-card"><span className="state-pulse"/><strong>Loading your private workspace…</strong></div>;
  if(error==="AUTH_REQUIRED")return <div className="state-card"><strong>Sign in to open your financial workspace.</strong><Link className="primary" href="/login">Sign in</Link></div>;
  if(error)return <div className="state-card error"><strong>Something needs attention</strong><span>{error}</span></div>;
  if(empty)return <div className="state-card"><strong>{empty}</strong><span>{emptyDetail??"Import a statement or add a record to get started."}</span></div>;
  return null;
}
