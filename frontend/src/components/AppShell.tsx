"use client";

import { useState } from "react";
import Link from "next/link";
import { Home, Landmark, Rows3, Users, ReceiptText, Plane, Scale, Settings, Search, Bell, Palette, X } from "lucide-react";

const nav = [
  ["Home", Home, "/"], ["Accounts & Import", Landmark, "/imports"], ["Transactions", Rows3, "/transactions"], ["People & Funds", Users, "/people"],
  ["Reimbursements", ReceiptText, "/reimbursements"], ["Trips", Plane, "/trips"], ["Reconciliation", Scale, "/reconciliation"], ["Settings", Settings, "/settings"],
] as const;
const themes = ["cobalt", "emerald", "amber", "coral", "indigo", "slate"];

export function AppShell({children,active,accent,onAccent}:{children:React.ReactNode;active:string;accent:string;onAccent:(value:string)=>void}) {
  const [themeOpen,setThemeOpen]=useState(false);
  function choose(value:string){ localStorage.setItem("expense-accent",value); onAccent(value); }
  return <div className="app-shell" data-accent={accent}>
    <aside className="side"><div className="brand"><span>₹</span><div><strong>Expense</strong><small>ANALYZER</small></div></div><nav>{nav.map(([label,Icon,href])=><Link href={href} className={active===label?"active":""} key={label}><Icon size={17}/>{label}</Link>)}</nav><div className="profile"><span>JS</span><div><strong>Jasmine S.</strong><small>Private workspace</small></div></div></aside>
    <div className="workspace"><div className="topbar"><div className="command"><Search size={15}/><span>Search transactions, people, accounts…</span><kbd>⌘ K</kbd></div><button aria-label="Notifications"><Bell size={17}/></button><button aria-label="Change colors" onClick={()=>setThemeOpen(true)}><Palette size={17}/></button></div><main>{children}</main></div>
    {themeOpen&&<div className="theme-panel"><div className="theme-title"><div><strong>Application color</strong><small>Choose the accent used throughout your workspace.</small></div><button onClick={()=>setThemeOpen(false)}><X size={17}/></button></div><div className="swatches">{themes.map(theme=><button key={theme} className={`swatch ${theme} ${accent===theme?"selected":""}`} onClick={()=>choose(theme)}><i/><span>{theme}</span></button>)}</div></div>}
  </div>
}
