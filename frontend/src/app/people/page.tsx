"use client";

import { FormEvent, useEffect, useState } from "react";
import { Plus, UserRound } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { WorkspaceState } from "@/components/WorkspaceState";
import { api, inr } from "@/lib/api";

type Person={id:number;name:string;relationship_label:string;notes:string;entrusted_balance:string;loan_balance:string};
type Detail={person:Person;entrusted_balance:string;loan_balance:string;transactions:{id:number;txn_date:string;description:string;amount:string;purpose:string}[]};

export default function People(){
 const [accent,setAccent]=useState(()=>typeof window==="undefined"?"cobalt":localStorage.getItem("expense-accent")??"cobalt"),[people,setPeople]=useState<Person[]>([]),[detail,setDetail]=useState<Detail>(),[error,setError]=useState(""),[loading,setLoading]=useState(true),[adding,setAdding]=useState(false);
 useEffect(()=>{api<Person[]>("/people").then(setPeople).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[]);
 async function open(id:number){setError("");try{setDetail(await api<Detail>(`/people/${id}`))}catch(reason){setError(reason instanceof Error?reason.message:"Person details could not be loaded")}}
 async function add(event:FormEvent<HTMLFormElement>){event.preventDefault();setError("");const data=new FormData(event.currentTarget);try{const person=await api<Person>("/people",{method:"POST",body:JSON.stringify({name:data.get("name"),relationship_label:data.get("relationship_label")})});setPeople(items=>[...items,person]);setAdding(false)}catch(reason){setError(reason instanceof Error?reason.message:"Person could not be added")}}
 return <AppShell accent={accent} onAccent={setAccent} active="People & Funds">
  <header className="page-head"><div><p className="kicker">People & funds</p><h1>People and balances</h1><p>Entrusted funds, loans given, and repayments received.</p></div><button className="primary" onClick={()=>setAdding(true)} disabled={adding}><Plus size={15}/>Add person</button></header>
  <WorkspaceState loading={loading} error={error} empty={!loading&&!people.length?"No people added yet.":undefined} emptyDetail="Add a person to begin linking entrusted funds or loan transactions."/>
  {adding&&<form className="inline-form" onSubmit={add}><label>Full name<input name="name" required placeholder="e.g. Alex Morgan" autoFocus/></label><label>Relationship <span className="muted-cell">Optional</span><input name="relationship_label" placeholder="e.g. Family, friend, colleague"/></label><div className="form-actions"><button type="button" className="secondary" onClick={()=>setAdding(false)}>Cancel</button><button className="primary">Save person</button></div></form>}
  <section className="people-grid">{people.map(person=><button className={`person-card ${detail?.person.id===person.id?"selected":""}`} key={person.id} onClick={()=>open(person.id)}><span className="avatar"><UserRound/></span><div><strong>{person.name}</strong><small>{person.relationship_label||"Person"}</small></div><dl><div><dt>Entrusted</dt><dd>{inr(person.entrusted_balance)}</dd></div><div><dt>Loan balance</dt><dd>{inr(person.loan_balance)}</dd></div></dl></button>)}</section>
  {detail&&<section className="detail-panel"><div className="section-head"><div><p className="kicker">Person detail</p><h2>{detail.person.name}</h2></div><div className="detail-totals"><span>Entrusted <strong>{inr(detail.entrusted_balance)}</strong></span><span>Loans <strong>{inr(detail.loan_balance)}</strong></span></div></div>{detail.transactions.length?<div className="table-scroll"><table className="responsive-table"><thead><tr><th>Date</th><th>Description</th><th>Meaning</th><th>Amount</th></tr></thead><tbody>{detail.transactions.map(row=><tr key={row.id}><td data-label="Date">{row.txn_date}</td><td data-label="Description" className="strong">{row.description}</td><td data-label="Meaning">{row.purpose.replaceAll("_"," ")}</td><td data-label="Amount" className={Number(row.amount)>=0?"money in":"money out"}>{inr(row.amount)}</td></tr>)}</tbody></table></div>:<WorkspaceState empty="No linked transactions" emptyDetail="Assign a ledger transaction to this person to see it here."/>}</section>}
 </AppShell>
}
