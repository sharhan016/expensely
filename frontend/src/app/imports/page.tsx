"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CreditCard, FileText, Files, Landmark, UploadCloud } from "lucide-react";
import { AppShell } from "@/components/AppShell";

type ImportRow = { id:number; txn_date:string; description:string; amount:string; purpose:string; confidence:string; selected:boolean; duplicate_of_id:number|null };
type Statement = { id:string; account_id:number|null; account_hint:string; detected_account_name:string; statement_kind:string; overlap_warning:boolean; original_filename:string; status:string; bank_slug:string; period_start:string|null; period_end:string|null; error_message:string; rows:ImportRow[] };
type Account = {id:number;name:string;bank_slug:string;account_hint:string;kind:string;currency:string;current_balance:string;statement_count:number};
const purposes = ["personal","business","income","transfer","loan_given","loan_repayment","entrusted","reimbursement","refund","other"];

export default function ImportsPage() {
  const [accent,setAccent]=useState(()=>typeof window==="undefined"?"cobalt":localStorage.getItem("expense-accent")??"cobalt");
  const [files,setFiles]=useState<File[]>([]), [statements,setStatements]=useState<Statement[]>([]);
  const [activeId,setActiveId]=useState<string>(), [selected,setSelected]=useState<Set<number>>(new Set());
  const [busy,setBusy]=useState(false), [message,setMessage]=useState("");
  const [pdfPassword,setPdfPassword]=useState("");
  const [accounts,setAccounts]=useState<Account[]>([]),[accountChoices,setAccountChoices]=useState<Record<string,string>>({}),[newAccountName,setNewAccountName]=useState("");
  const input=useRef<HTMLInputElement>(null), active=statements.find(item=>item.id===activeId)??statements[0];
  const accountLabel=(statement:Statement)=>statement.detected_account_name||`${statement.bank_slug==="unknown"?"Bank":statement.bank_slug.toUpperCase()} ${statement.statement_kind==="credit_card"?"Credit Card":"Account"}`;
  useEffect(()=>{fetch("/api/accounts").then(response=>response.ok?response.json():[]).then(setAccounts)},[]);
  function accept(incoming:FileList|null){if(incoming)setFiles(current=>[...current,...Array.from(incoming)].slice(0,20));}
  async function upload(){
    if(!files.length)return; setBusy(true); setMessage("");
    const body=new FormData(); files.forEach(file=>body.append("files",file));
    const response=await fetch("/api/imports",{method:"POST",body});
    if(response.ok){const data:Statement[]=await response.json();setStatements(data);setActiveId(data[0]?.id);setAccountChoices(Object.fromEntries(data.map(item=>[item.id,item.account_id?String(item.account_id):"new"])));setFiles([]);setNewAccountName(data[0]?.detected_account_name??"");setMessage(`${data.length} statement${data.length===1?"":"s"} ready to review.`);}
    else{const error=await response.json().catch(()=>({detail:"Upload failed"}));setMessage(error.detail??"Upload failed");}
    setBusy(false);
  }
  async function bulkClassify(purpose:string){
    if(!active||!selected.size)return;
    const response=await fetch(`/api/imports/${active.id}/rows`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({row_ids:[...selected],purpose})});
    if(response.ok){setStatements(items=>items.map(statement=>statement.id!==active.id?statement:{...statement,rows:statement.rows.map(row=>selected.has(row.id)?{...row,purpose}:row)}));setSelected(new Set());}
  }
  async function confirm(){
    if(!active)return; setBusy(true); setMessage("");
    const choice=accountChoices[active.id]??(active.account_id?String(active.account_id):"new");
    const response=await fetch(`/api/imports/${active.id}/confirm`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(choice==="new"?{account_name:newAccountName||accountLabel(active)}:{account_id:Number(choice)})});
    if(response.ok){const result=await response.json();setMessage(`${result.imported} transactions added to the ledger. ${result.skipped_duplicates} duplicates skipped.`);const createdOrUsed=await fetch("/api/accounts").then(value=>value.json());setAccounts(createdOrUsed);setStatements(items=>items.filter(item=>item.id!==active.id));setAccountChoices(current=>({...current,...Object.fromEntries(statements.filter(item=>item.id!==active.id&&item.bank_slug===active.bank_slug&&item.account_hint&&item.account_hint===active.account_hint).map(item=>[item.id,String(result.account_id)]))}));setActiveId(undefined);setNewAccountName("");}
    else{const error=await response.json().catch(()=>({detail:"Confirmation failed"}));setMessage(error.detail??"Confirmation failed");}
    setBusy(false);
  }
  async function unlock(){
    if(!active||!pdfPassword)return; setBusy(true); setMessage("");
    const response=await fetch(`/api/imports/${active.id}/unlock`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:pdfPassword})});
    setPdfPassword("");
    if(response.ok){const updated:Statement=await response.json();setStatements(items=>items.map(item=>item.id===updated.id?updated:item));setMessage("PDF unlocked. The password was not stored.");}
    else{setMessage("That password did not unlock the PDF.");}
    setBusy(false);
  }
  return <AppShell accent={accent} onAccent={setAccent} active="Accounts & Import">
    <header className="page-head"><div><p className="kicker">Accounts & import</p><h1>Bring every account into one ledger.</h1><p>Add several statement periods at once. Nothing enters your ledger until you confirm it.</p></div></header>
    <section className="accounts-section">
      <div className="section-head"><div><p className="kicker">Your accounts</p><h2>{accounts.length?`${accounts.length} connected account${accounts.length===1?"":"s"}`:"No connected accounts yet"}</h2></div><span className="accounts-caption">Created automatically from confirmed statements</span></div>
      {accounts.length>0?<div className="account-grid">{accounts.map(account=><article className="account-card" key={account.id}><div className="account-card-icon">{account.kind==="credit_card"?<CreditCard/>:<Landmark/>}</div><div className="account-card-main"><span>{account.bank_slug.toUpperCase()} · {account.kind.replaceAll("_"," ")}</span><h3>{account.name}</h3><p>{account.account_hint?`•••• ${account.account_hint.slice(-4)}`:"Account identity unavailable"}</p></div><div className="account-card-balance"><span>Ledger balance</span><strong>{Number(account.current_balance)<0?"−":""}₹{Math.abs(Number(account.current_balance)).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2})}</strong><small>{account.statement_count} statement{account.statement_count===1?"":"s"}</small></div></article>)}</div>:<div className="accounts-empty"><Landmark/><div><strong>Your first account will appear here</strong><p>Upload a statement and confirm its detected identity. There is no separate account setup.</p></div></div>}
    </section>
    {!statements.length?<>
      <section className="upload-zone" onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();accept(event.dataTransfer.files)}} onClick={()=>input.current?.click()}><div className="upload-mark"><UploadCloud/></div><h2>Drop bank statements here</h2><p>PDF, CSV or TXT · up to 20 files · 20 MB each</p><button className="secondary" type="button">Choose statements</button><input ref={input} type="file" multiple accept=".pdf,.csv,.txt" hidden onChange={event=>accept(event.target.files)}/></section>
      <div className="import-assurance"><span><Files/>Multiple accounts and periods</span><span><Landmark/>Bank and account detection</span><span><Check/>Review before ledger</span></div>
      {files.length>0&&<section className="queue-card"><div className="section-head"><div><p className="kicker">Upload queue</p><h2>{files.length} statement{files.length===1?"":"s"}</h2></div><button className="primary" onClick={upload} disabled={busy}>{busy?"Reading statements…":"Extract transactions"}<ArrowRight size={14}/></button></div>{files.map((file,index)=><div className="queued-file" key={`${file.name}-${index}`}><FileText/><div><strong>{file.name}</strong><small>{(file.size/1024/1024).toFixed(2)} MB</small></div><button onClick={()=>setFiles(items=>items.filter((_,itemIndex)=>index!==itemIndex))}>Remove</button></div>)}</section>}
      {message&&<p className="form-message">{message}</p>}
    </>:<section className="review-layout">
      <aside className="statement-list"><p className="kicker">This upload</p><h2>{statements.length} statements</h2>{statements.map(statement=><button key={statement.id} className={active?.id===statement.id?"selected":""} onClick={()=>{setActiveId(statement.id);setSelected(new Set());setNewAccountName(statement.detected_account_name)}}><FileText/><span><strong>{statement.original_filename}</strong><small>{statement.bank_slug==="unknown"?"Bank needs confirmation":statement.bank_slug.toUpperCase()} · {statement.rows.length} rows{statement.overlap_warning?" · overlaps existing period":""}</small></span><i className={statement.overlap_warning?"warning":statement.status}/></button>)}</aside>
      <div className="review-card">{active&&<>{active.status==="password_required"?<div className="password-prompt"><FileText/><p className="kicker">Protected statement</p><h2>Enter the PDF password</h2><p>The password is used in memory to unlock this file. It is never logged or saved.</p><div><input type="password" value={pdfPassword} onChange={event=>setPdfPassword(event.target.value)} autoComplete="off" placeholder="PDF password"/><button className="primary" onClick={unlock} disabled={busy||!pdfPassword}>{busy?"Unlocking…":"Unlock and extract"}</button></div></div>:<><div className="review-title"><div><p className="kicker">Extraction result</p><h2>{active.original_filename}</h2><p>{active.period_start??"Period not detected"} — {active.period_end??"review dates"}</p></div><button className="primary" onClick={confirm} disabled={busy}>{busy?"Saving…":"Confirm to ledger"} <ArrowRight size={14}/></button></div>
        <div className="detected-account"><div className="detected-account-icon">{active.statement_kind==="credit_card"?<CreditCard/>:<Landmark/>}</div><div><span className="detected-badge">Account detected</span><h3>{accountLabel(active)}</h3><p>{active.account_hint?`Card ending ${active.account_hint.slice(-4)}`:"Account number needs confirmation"} · {active.rows.length} transactions · {active.period_start??"Period unavailable"} to {active.period_end??"review dates"}</p></div></div>
        <div className="account-binding"><div><Landmark/><span><strong>{active.account_id?"Matched to an existing account":"Where should this statement go?"}</strong><small>{active.account_id?"We found this card in your accounts":"A new account will be created after confirmation"}</small></span></div><select value={accountChoices[active.id]??(active.account_id?String(active.account_id):"new")} onChange={event=>setAccountChoices(current=>({...current,[active.id]:event.target.value}))}><option value="new">Create a new account</option>{accounts.map(account=><option value={account.id} key={account.id}>{account.name} · {account.account_hint.slice(-4)}</option>)}</select>{(accountChoices[active.id]??(active.account_id?String(active.account_id):"new"))==="new"&&<input value={newAccountName||active.detected_account_name} onChange={event=>setNewAccountName(event.target.value)} placeholder={accountLabel(active)}/>}</div>
        <div className="review-toolbar"><strong>{selected.size?`${selected.size} selected`:`${active.rows.length} extracted`}</strong><span>{active.rows.filter(row=>row.duplicate_of_id).length} possible duplicates</span>{active.overlap_warning&&<span className="overlap-alert">Statement period overlaps an earlier import</span>}<select disabled={!selected.size} defaultValue="" onChange={event=>event.target.value&&bulkClassify(event.target.value)}><option value="">Classify selected…</option>{purposes.map(purpose=><option key={purpose} value={purpose}>{purpose.replaceAll("_"," ")}</option>)}</select></div>
        <div className="table-scroll"><table><thead><tr><th><input type="checkbox" aria-label="Select all" checked={active.rows.length>0&&selected.size===active.rows.length} onChange={event=>setSelected(event.target.checked?new Set(active.rows.map(row=>row.id)):new Set())}/></th><th>Date</th><th>Bank description</th><th>Classification</th><th>Evidence</th><th>Amount</th></tr></thead><tbody>{active.rows.map(row=><tr key={row.id}><td><input type="checkbox" checked={selected.has(row.id)} onChange={()=>setSelected(current=>{const next=new Set(current);if(next.has(row.id)){next.delete(row.id)}else{next.add(row.id)}return next})}/></td><td>{row.txn_date}</td><td className="strong">{row.description}</td><td><span className={`purpose-chip ${row.purpose}`}>{row.purpose.replaceAll("_"," ")}</span></td><td>{row.duplicate_of_id?<span className="duplicate-chip">Possible duplicate</span>:row.confidence==="confident"?<span className="confidence-chip">Confident match</span>:row.confidence==="suggested"?<span className="suggestion-chip">Suggested · confirm</span>:<span className="muted-cell">Needs review</span>}</td><td className={Number(row.amount)>=0?"money in":"money out"}>{Number(row.amount)>=0?"+":"−"}₹{Math.abs(Number(row.amount)).toLocaleString("en-IN",{minimumFractionDigits:2})}</td></tr>)}</tbody></table></div>
      </>}</>}</div>
    </section>}
  </AppShell>;
}
