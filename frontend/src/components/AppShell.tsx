"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bot, BookOpenText, Home, Landmark, Menu, Palette, Plane, ReceiptText, Rows3, Scale, Settings, Users, Waypoints, X } from "lucide-react";

const navGroups = [
  { label: "Overview", items: [["Home", Home, "/"], ["Assistant", Bot, "/assistant"]] },
  { label: "Ledger", items: [["Accounts & Import", Landmark, "/imports"], ["Transactions", Rows3, "/transactions"], ["Flows", Waypoints, "/flows"], ["Reconciliation", Scale, "/reconciliation"]] },
  { label: "Manage", items: [["People & Funds", Users, "/people"], ["Reimbursements", ReceiptText, "/reimbursements"], ["Trips", Plane, "/trips"], ["Settings", Settings, "/settings"]] },
] as const;
const themes = ["cobalt", "emerald", "amber", "coral", "indigo", "slate"];

export function AppShell({ children, active, accent, onAccent }: { children: React.ReactNode; active: string; accent: string; onAccent: (value: string) => void }) {
  const [themeOpen, setThemeOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const themeTrigger = useRef<HTMLButtonElement>(null);
  const themeClose = useRef<HTMLButtonElement>(null);
  const themePanel = useRef<HTMLElement>(null);

  useEffect(() => { fetch("/api/auth/me").then(response => response.ok ? response.json() : null).then(user => setDisplayName(user?.display_name ?? "")); }, []);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 900px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!themeOpen) return;
    themeClose.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") { setThemeOpen(false); themeTrigger.current?.focus(); return; }
      if (event.key === "Tab") {
        const controls = themePanel.current?.querySelectorAll<HTMLElement>("button:not([disabled])");
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [themeOpen]);

  const initials = displayName.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]?.toUpperCase()).join("") || "—";
  function choose(value: string) { localStorage.setItem("expense-accent", value); onAccent(value); }

  return <div className="app-shell" data-accent={accent}>
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <aside className={`side ${navOpen ? "open" : ""}`} aria-label="Primary navigation" aria-hidden={mobile && !navOpen} inert={mobile && !navOpen ? true : undefined}>
      <div className="side-head">
        <Link className="brand" href="/" onClick={() => setNavOpen(false)} aria-label="Expense Analyzer home"><span aria-hidden="true">₹</span><div><strong>Expense</strong><small>ANALYZER</small></div></Link>
        <button className="icon-button side-close" aria-label="Close navigation" onClick={() => setNavOpen(false)}><X /></button>
      </div>
      <nav>{navGroups.map(group => <section className="nav-group" key={group.label} aria-labelledby={`nav-${group.label}`}><h2 id={`nav-${group.label}`}>{group.label}</h2>{group.items.map(([label, Icon, href]) => <Link href={href} className={active === label ? "active" : ""} aria-current={active === label ? "page" : undefined} key={label} onClick={() => setNavOpen(false)}><Icon aria-hidden="true" /><span>{label}</span></Link>)}</section>)}</nav>
      <div className="profile"><span aria-hidden="true">{initials}</span><div><strong>{displayName || "Your account"}</strong><small>Private workspace</small></div></div>
    </aside>
    {navOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
    <div className="workspace">
      <header className="topbar"><button className="icon-button menu-button" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen(true)}><Menu /></button><div className="topbar-context"><BookOpenText aria-hidden="true" /><span>{active}</span></div><button ref={themeTrigger} className="icon-button" aria-label="Change application color" aria-haspopup="dialog" onClick={() => setThemeOpen(true)}><Palette /></button></header>
      <main id="main-content" tabIndex={-1}>{children}</main>
    </div>
    {themeOpen && <div className="dialog-layer" role="presentation" onMouseDown={event => { if(event.target===event.currentTarget){setThemeOpen(false);themeTrigger.current?.focus()} }}><section ref={themePanel} className="theme-panel" role="dialog" aria-modal="true" aria-labelledby="theme-title"><div className="theme-title"><div><p className="kicker">Appearance</p><h2 id="theme-title">Application color</h2><p>Choose the accent used for actions and focus.</p></div><button ref={themeClose} className="icon-button" aria-label="Close color chooser" onClick={() => { setThemeOpen(false); themeTrigger.current?.focus(); }}><X /></button></div><div className="swatches" role="list" aria-label="Accent colors">{themes.map(theme => <button key={theme} className={`swatch ${theme} ${accent === theme ? "selected" : ""}`} aria-pressed={accent === theme} onClick={() => choose(theme)}><i aria-hidden="true" /><span>{theme}</span></button>)}</div></section></div>}
  </div>;
}
