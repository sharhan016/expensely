"use client";

import { useEffect,useMemo,useState } from "react";
import { AppShell } from "@/components/AppShell";
import { WorkspaceState } from "@/components/WorkspaceState";
import { api,inr } from "@/lib/api";
import { runningBalances } from "@/lib/ledger";

type Tx={id:number;account_id:number;txn_date:string;description:string;amount:string;purpose:string;person_id:number|null;employer_id:number|null;trip_id:number|null};
type Account={id:number;name:string;opening_balance:string};
type Person={id:number;name:string};type Employer={id:number;name:string;is_default:boolean};
const purposes=["personal","business","income","transfer","loan_given","loan_repayment","entrusted","reimbursement","refund","other"];

export default function Transactions(){
 const [accent,setAccent]=useState(()=>typeof window==="undefined"?"cobalt":localStorage.getItem("expense-accent")??"cobalt"),[rows,setRows]=useState<Tx[]>([]),[accounts,setAccounts]=useState<Account[]>([]),[people,setPeople]=useState<Person[]>([]),[employers,setEmployers]=useState<Employer[]>([]),[error,setError]=useState(""),[loading,setLoading]=useState(true),[filter,setFilter]=useState("all"),[draftPurposes,setDraftPurposes]=useState<Record<number,string>>({});
 useEffect(()=>{Promise.all([api<Tx[]>("/transactions"),api<Account[]>("/accounts"),api<Person[]>("/people"),api<Employer[]>("/employers")]).then(([t,a,p,e])=>{setRows(t);setAccounts(a);setPeople(p);setEmployers(e)}).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[]);
 const accountMap=useMemo(()=>Object.fromEntries(accounts.map(a=>[a.id,a])),[accounts]);
 const balances=useMemo(()=>runningBalances(accounts,rows),[rows,accounts]);
 async function update(row:Tx,changes:Partial<Tx>){setError("");try{const updated=await api<Tx>(`/transactions/${row.id}`,{method:"PATCH",body:JSON.stringify(changes)});setRows(items=>items.map(item=>item.id===row.id?updated:item));return updated}catch(reason){setError(reason instanceof Error?reason.message:"Transaction could not be updated")}}
 const visible=filter==="all"?rows:rows.filter(row=>row.account_id===Number(filter));
 async function changePurpose(row:Tx,purpose:string){if(["entrusted","loan_given","loan_repayment"].includes(purpose)&&!row.person_id){if(people.length===1){await update(row,{purpose,person_id:people[0].id});return}setDraftPurposes(current=>({...current,[row.id]:purpose}));return}await update(row,{purpose});setDraftPurposes(current=>{const next={...current};delete next[row.id];return next})}
 function responsibility(row:Tx,purpose:string){
   if(["entrusted","loan_given","loan_repayment"].includes(purpose))return <select aria-label={`Related person for ${row.description}`} className="inline-select" value={row.person_id??""} onChange={async e=>{const person_id=Number(e.target.value)||null;if(!person_id)return;const updated=await update(row,{person_id,purpose});if(updated)setDraftPurposes(current=>{const next={...current};delete next[row.id];return next})}}><option value="">Choose person</option>{people.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>;
   if(["business","reimbursement"].includes(purpose)){const defaultEmployer=employers.find(e=>e.is_default);if(employers.length<=1)return <span className="muted-cell">{defaultEmployer?.name??"Add company"}</span>;return <select aria-label="Related company" className="inline-select" value={row.employer_id??defaultEmployer?.id??""} onChange={e=>update(row,{employer_id:Number(e.target.value)||null})}>{employers.map(e=><option key={e.id} value={e.id}>{e.name}</option>)}</select>}
   return <span className="muted-cell">—</span>;
 }
 return <AppShell accent={accent} onAccent={setAccent} active="Transactions"><header className="page-head"><div><p className="kicker">Unified ledger</p><h1>Transaction ledger</h1><p>Bank movement stays separate from its financial meaning.</p></div><select aria-label="Filter by account" className="page-select" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All accounts</option>{accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></header><WorkspaceState loading={loading} error={error} empty={!loading&&!visible.length?"No transactions yet.":undefined}/>{visible.length>0&&<section className="ledger-card"><div className="table-scroll"><table className="responsive-table"><thead><tr><th>Date</th><th>Description</th><th>Account</th><th>Purpose</th><th>Person / company</th><th>Amount</th><th>Running balance</th></tr></thead><tbody>{visible.map(row=>{const effectivePurpose=draftPurposes[row.id]??row.purpose;return <tr key={row.id}><td data-label="Date">{row.txn_date}</td><td data-label="Description" className="strong">{row.description}</td><td data-label="Account"><span className="account-chip">{accountMap[row.account_id]?.name??"Account"}</span></td><td data-label="Purpose"><select aria-label={`Purpose for ${row.description}`} className="inline-select" value={effectivePurpose} onChange={e=>changePurpose(row,e.target.value)}>{purposes.map(p=><option key={p} value={p}>{p.replaceAll("_"," ")}</option>)}</select></td><td data-label="Person or company">{responsibility(row,effectivePurpose)}</td><td data-label="Amount" className={Number(row.amount)>=0?"money in":"money out"}>{inr(row.amount)}</td><td data-label="Running balance" className="money">{inr(balances[row.id]??0)}</td></tr>})}</tbody></table></div></section>}</AppShell>
}
