"use client";

import { useEffect,useMemo,useState } from "react";
import { AppShell } from "@/components/AppShell";
import { WorkspaceState } from "@/components/WorkspaceState";
import { api,inr } from "@/lib/api";
type Tx={id:number;account_id:number;txn_date:string;description:string;amount:string;purpose:string;bank_balance:string|null};
type Account={id:number;name:string;opening_balance:string};
const purposes=["personal","business","income","transfer","loan_given","loan_repayment","entrusted","reimbursement","refund","other"];
export default function Transactions(){
 const [accent,setAccent]=useState(()=>typeof window==="undefined"?"cobalt":localStorage.getItem("expense-accent")??"cobalt"),[rows,setRows]=useState<Tx[]>([]),[accounts,setAccounts]=useState<Account[]>([]),[error,setError]=useState(""),[loading,setLoading]=useState(true),[filter,setFilter]=useState("all");
 useEffect(()=>{Promise.all([api<Tx[]>("/transactions"),api<Account[]>("/accounts")]).then(([t,a])=>{setRows(t);setAccounts(a)}).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[]);
 const accountMap=useMemo(()=>Object.fromEntries(accounts.map(a=>[a.id,a])),[accounts]);
 const balances=useMemo(()=>{const totals:Record<number,number>={};accounts.forEach(a=>totals[a.id]=Number(a.opening_balance));const result:Record<number,number>={};[...rows].reverse().forEach(row=>{totals[row.account_id]=(totals[row.account_id]??0)+Number(row.amount);result[row.id]=totals[row.account_id]});return result},[rows,accounts]);
 async function classify(row:Tx,purpose:string){const updated=await api<Tx>(`/transactions/${row.id}`,{method:"PATCH",body:JSON.stringify({purpose})});setRows(items=>items.map(item=>item.id===row.id?updated:item))}
 const visible=filter==="all"?rows:rows.filter(row=>row.account_id===Number(filter));
 return <AppShell accent={accent} onAccent={setAccent} active="Transactions"><header className="page-head"><div><p className="kicker">Unified ledger</p><h1>Every movement, in order.</h1><p>Bank movement stays separate from its financial meaning.</p></div><select className="page-select" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All accounts</option>{accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></header><WorkspaceState loading={loading} error={error} empty={!loading&&!visible.length?"No transactions yet.":undefined}/>{visible.length>0&&<section className="ledger-card"><div className="table-scroll"><table><thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Purpose</th><th>Amount</th><th>Running balance</th></tr></thead><tbody>{visible.map(row=><tr key={row.id}><td>{row.txn_date}</td><td className="strong">{row.description}</td><td><span className="account-chip">{accountMap[row.account_id]?.name??"Account"}</span></td><td><select className="inline-select" value={row.purpose} onChange={e=>classify(row,e.target.value)}>{purposes.map(p=><option key={p} value={p}>{p.replaceAll("_"," ")}</option>)}</select></td><td className={Number(row.amount)>=0?"money in":"money out"}>{inr(row.amount)}</td><td className="money">{inr(balances[row.id]??0)}</td></tr>)}</tbody></table></div></section>}</AppShell>
}
