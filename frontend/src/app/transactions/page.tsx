"use client";

import { useEffect,useMemo,useState } from "react";
import { AppShell } from "@/components/AppShell";
import { WorkspaceState } from "@/components/WorkspaceState";
import { api,inr } from "@/lib/api";
import { runningBalances } from "@/lib/ledger";

type Tx={id:number;account_id:number;txn_date:string;description:string;amount:string;purpose:string;person_id:number|null;employer_id:number|null;trip_id:number|null};
type Account={id:number;name:string;opening_balance:string};
type Person={id:number;name:string};type Employer={id:number;name:string;is_default:boolean};type Trip={trip:{id:number;name:string}};
const purposes=["personal","business","income","transfer","loan_given","loan_repayment","entrusted","reimbursement","refund","other"];

export default function Transactions(){
 const [accent,setAccent]=useState(()=>typeof window==="undefined"?"cobalt":localStorage.getItem("expense-accent")??"cobalt"),[rows,setRows]=useState<Tx[]>([]),[accounts,setAccounts]=useState<Account[]>([]),[people,setPeople]=useState<Person[]>([]),[employers,setEmployers]=useState<Employer[]>([]),[trips,setTrips]=useState<Trip[]>([]),[error,setError]=useState(""),[loading,setLoading]=useState(true),[filter,setFilter]=useState("all");
 useEffect(()=>{Promise.all([api<Tx[]>("/transactions"),api<Account[]>("/accounts"),api<Person[]>("/people"),api<Employer[]>("/employers"),api<Trip[]>("/trips")]).then(([t,a,p,e,r])=>{setRows(t);setAccounts(a);setPeople(p);setEmployers(e);setTrips(r)}).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[]);
 const accountMap=useMemo(()=>Object.fromEntries(accounts.map(a=>[a.id,a])),[accounts]);
 const balances=useMemo(()=>runningBalances(accounts,rows),[rows,accounts]);
 async function update(row:Tx,changes:Partial<Tx>){const updated=await api<Tx>(`/transactions/${row.id}`,{method:"PATCH",body:JSON.stringify(changes)});setRows(items=>items.map(item=>item.id===row.id?updated:item))}
 const visible=filter==="all"?rows:rows.filter(row=>row.account_id===Number(filter));
 function responsibility(row:Tx){
   if(["entrusted","loan_given","loan_repayment"].includes(row.purpose))return <select aria-label="Related person" className="inline-select" value={row.person_id??""} onChange={e=>update(row,{person_id:Number(e.target.value)||null})}><option value="">Choose person</option>{people.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>;
   if(["business","reimbursement"].includes(row.purpose)){const defaultEmployer=employers.find(e=>e.is_default);if(employers.length<=1)return <span className="muted-cell">{defaultEmployer?.name??"Add company"}</span>;return <select aria-label="Related company" className="inline-select" value={row.employer_id??defaultEmployer?.id??""} onChange={e=>update(row,{employer_id:Number(e.target.value)||null})}>{employers.map(e=><option key={e.id} value={e.id}>{e.name}</option>)}</select>}
   return <select aria-label="Related trip" className="inline-select" value={row.trip_id??""} onChange={e=>update(row,{trip_id:Number(e.target.value)||null})}><option value="">No trip</option>{trips.map(item=><option key={item.trip.id} value={item.trip.id}>{item.trip.name}</option>)}</select>;
 }
 return <AppShell accent={accent} onAccent={setAccent} active="Transactions"><header className="page-head"><div><p className="kicker">Unified ledger</p><h1>Every movement, in order.</h1><p>Bank movement stays separate from its financial meaning and responsible person.</p></div><select className="page-select" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All accounts</option>{accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></header><WorkspaceState loading={loading} error={error} empty={!loading&&!visible.length?"No transactions yet.":undefined}/>{visible.length>0&&<section className="ledger-card"><div className="table-scroll"><table><thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Purpose</th><th>Person / company / trip</th><th>Amount</th><th>Running balance</th></tr></thead><tbody>{visible.map(row=><tr key={row.id}><td>{row.txn_date}</td><td className="strong">{row.description}</td><td><span className="account-chip">{accountMap[row.account_id]?.name??"Account"}</span></td><td><select className="inline-select" value={row.purpose} onChange={e=>update(row,{purpose:e.target.value})}>{purposes.map(p=><option key={p} value={p}>{p.replaceAll("_"," ")}</option>)}</select></td><td>{responsibility(row)}</td><td className={Number(row.amount)>=0?"money in":"money out"}>{inr(row.amount)}</td><td className="money">{inr(balances[row.id]??0)}</td></tr>)}</tbody></table></div></section>}</AppShell>
}
