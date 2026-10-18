"use client";

import { useEffect, useState } from "react";
import { Landmark, WalletCards, HandCoins, CircleAlert, ArrowUpRight, Minus, Equal } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import Link from "next/link";
import { api, inr } from "@/lib/api";

export default function Home() {
  const [accent, setAccent] = useState(() =>
    typeof window === "undefined" ? "cobalt" : localStorage.getItem("expense-accent") ?? "cobalt",
  );
  const [summary,setSummary]=useState({bank_balance:0,credit_card_balance:0,entrusted_balance:0,available_balance:0,company_receivable:0,loan_balance:0,unresolved_count:0,account_count:0,credit_card_count:0});
  const [activity,setActivity]=useState<{id:number;txn_date:string;description:string;account_id:number;amount:string;purpose:string}[]>([]),[accounts,setAccounts]=useState<Record<number,string>>({}),[name,setName]=useState("there");
  useEffect(()=>{Promise.all([api<typeof summary>("/dashboard"),api<typeof activity>("/transactions"),api<{id:number;name:string}[]>("/accounts"),api<{display_name:string}>("/auth/me")]).then(([s,t,a,u])=>{setSummary(s);setActivity(t.slice(0,6));setAccounts(Object.fromEntries(a.map(item=>[item.id,item.name])));setName(u.display_name.split(" ")[0])}).catch(()=>{})},[]);
  return (
    <AppShell accent={accent} onAccent={setAccent} active="Home">
      <header className="page-head">
        <div><p className="kicker">Overview</p><h1>Financial overview</h1><p>Signed in as {name}. Review account balances, liabilities, and outstanding items.</p></div>
        <Link className="primary" href="/imports">Import statements <ArrowUpRight size={15}/></Link>
      </header>

      <section className="fund-equation" aria-label="Available funds calculation">
        <Metric icon={<Landmark/>} label="Total bank balance" value={inr(summary.bank_balance)} detail={`Across ${summary.account_count} accounts`} />
        <Minus className="operator"/>
        <Metric icon={<HandCoins/>} label="Entrusted funds" value={inr(summary.entrusted_balance)} detail="Belongs to other people" tone="ochre"/>
        <Equal className="operator"/>
        <Metric icon={<WalletCards/>} label="Your available funds" value={inr(summary.available_balance)} detail="For personal use" tone="green"/>
      </section>

      <section className="signal-grid">
        <Signal label="Credit card balance" value={inr(summary.credit_card_balance)} detail={`Amount payable across ${summary.credit_card_count} card${summary.credit_card_count===1?"":"s"}`} icon={<WalletCards/>}/>
        <Signal label="Company receivable" value={inr(summary.company_receivable)} detail="Business expenses still recoverable" icon={<WalletCards/>}/>
        <Signal label="Loans outstanding" value={inr(summary.loan_balance)} detail="Across people" icon={<HandCoins/>}/>
        <Signal label="Unresolved items" value={String(summary.unresolved_count)} detail="Review before reconciliation" icon={<CircleAlert/>} warning/>
      </section>

      <section className="ledger-card">
        <div className="section-head"><div><p className="kicker">Latest movement</p><h2>Recent activity</h2></div><Link className="text-button" href="/transactions">View full ledger →</Link></div>
        <div className="table-scroll"><table><thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Purpose</th><th>Amount</th></tr></thead><tbody>{activity.map((row) => <tr key={row.id}><td>{row.txn_date}</td><td className="strong">{row.description}</td><td><span className="account-chip">{accounts[row.account_id]??"Account"}</span></td><td>{row.purpose.replaceAll("_"," ")}</td><td className={Number(row.amount)>=0 ? "money in" : "money out"}>{inr(row.amount)}</td></tr>)}</tbody></table></div>
      </section>
    </AppShell>
  );
}

function Metric({icon,label,value,detail,tone="blue"}:{icon:React.ReactNode;label:string;value:string;detail:string;tone?:string}) { return <article className={`metric ${tone}`}><div className="metric-icon">{icon}</div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article> }
function Signal({label,value,detail,icon,warning=false}:{label:string;value:string;detail:string;icon:React.ReactNode;warning?:boolean}) { return <article className={`signal ${warning?"warning":""}`}><div>{icon}<span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article> }
