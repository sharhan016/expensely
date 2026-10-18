"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { Landmark } from "lucide-react";
import { api } from "@/lib/api";

export default function LoginPage(){
  const router=useRouter(),[mode,setMode]=useState<"login"|"register">("login"),[error,setError]=useState("");
  async function submit(event:FormEvent<HTMLFormElement>){event.preventDefault();setError("");const data=new FormData(event.currentTarget);try{await api(`/auth/${mode}`,{method:"POST",body:JSON.stringify({email:data.get("email"),password:data.get("password"),...(mode==="register"?{display_name:data.get("name")}: {})})});router.push("/");router.refresh()}catch(reason){setError(reason instanceof Error?reason.message:"Unable to sign in")}}
  return <main className="auth-page"><section className="auth-intro"><div className="brand"><span>₹</span><div><strong>Expense</strong><small>ANALYZER</small></div></div><div><Landmark/><p className="kicker">Private by design</p><h1>Every account.<br/>Every responsibility.<br/>One honest balance.</h1><p>Your statements stay in your own installation. Financial records are only changed after your confirmation.</p></div></section><section className="auth-form"><form onSubmit={submit}><p className="kicker">{mode==="login"?"Welcome back":"Create your workspace"}</p><h2>{mode==="login"?"Sign in":"Start securely"}</h2>{mode==="register"&&<label>Your name<input name="name" required autoComplete="name"/></label>}<label>Email<input name="email" type="email" required autoComplete="email"/></label><label>Password<input name="password" type="password" required minLength={10} autoComplete={mode==="login"?"current-password":"new-password"}/></label>{error&&<p className="form-error">{error}</p>}<button className="primary" type="submit">{mode==="login"?"Open workspace":"Create workspace"}</button><button className="auth-switch" type="button" onClick={()=>setMode(mode==="login"?"register":"login")}>{mode==="login"?"New here? Create an account":"Already registered? Sign in"}</button></form></section></main>
}
