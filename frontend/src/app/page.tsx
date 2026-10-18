"use client";

import { useState } from "react";
import { Landmark, WalletCards, HandCoins, CircleAlert, ArrowUpRight, Minus, Equal } from "lucide-react";
import { AppShell } from "@/components/AppShell";

const activity = [
  ["31 Oct", "Salary credit", "HDFC Salary", "—", "+₹1,25,000"],
  ["30 Oct", "Office expense · Figma", "ICICI Credit Card", "Acuver Consulting", "−₹4,320"],
  ["29 Oct", "Loan repayment", "ICICI Savings", "Fatah Khan", "+₹10,000"],
  ["28 Oct", "Restaurant", "Yes Bank", "Sara Ahmed", "−₹2,450"],
];

export default function Home() {
  const [accent, setAccent] = useState(() =>
    typeof window === "undefined" ? "cobalt" : localStorage.getItem("expense-accent") ?? "cobalt",
  );
  return (
    <AppShell accent={accent} onAccent={setAccent} active="Home">
      <header className="page-head">
        <div><p className="kicker">31 October 2026</p><h1>Good morning, Jasmine.</h1><p>Your money, with everyone&apos;s share made explicit.</p></div>
        <button className="primary">Import statements <ArrowUpRight size={15}/></button>
      </header>

      <section className="fund-equation" aria-label="Available funds calculation">
        <Metric icon={<Landmark/>} label="Total bank balance" value="₹4,28,350" detail="Across 3 accounts" />
        <Minus className="operator"/>
        <Metric icon={<HandCoins/>} label="Entrusted funds" value="₹1,20,000" detail="Held for 2 people" tone="ochre"/>
        <Equal className="operator"/>
        <Metric icon={<WalletCards/>} label="Your available funds" value="₹3,08,350" detail="For personal use" tone="green"/>
      </section>

      <section className="signal-grid">
        <Signal label="Company receivable" value="₹68,560" detail="12 expenses · Acuver Consulting" icon={<WalletCards/>}/>
        <Signal label="Loans outstanding" value="₹50,000" detail="2 people" icon={<HandCoins/>}/>
        <Signal label="Unresolved items" value="6" detail="Review before reconciliation" icon={<CircleAlert/>} warning/>
      </section>

      <section className="ledger-card">
        <div className="section-head"><div><p className="kicker">Latest movement</p><h2>Recent activity</h2></div><button className="text-button">View full ledger →</button></div>
        <div className="table-scroll"><table><thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Person / company</th><th>Amount</th></tr></thead><tbody>{activity.map((row) => <tr key={row.join("")}><td>{row[0]}</td><td className="strong">{row[1]}</td><td><span className="account-chip">{row[2]}</span></td><td>{row[3]}</td><td className={row[4].startsWith("+") ? "money in" : "money out"}>{row[4]}</td></tr>)}</tbody></table></div>
      </section>
    </AppShell>
  );
}

function Metric({icon,label,value,detail,tone="blue"}:{icon:React.ReactNode;label:string;value:string;detail:string;tone?:string}) { return <article className={`metric ${tone}`}><div className="metric-icon">{icon}</div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article> }
function Signal({label,value,detail,icon,warning=false}:{label:string;value:string;detail:string;icon:React.ReactNode;warning?:boolean}) { return <article className={`signal ${warning?"warning":""}`}><div>{icon}<span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article> }
