export async function api<T>(path:string, init?:RequestInit):Promise<T>{
  const response=await fetch(`/api${path}`,{credentials:"include",...init,headers:{...(init?.body instanceof FormData?{}:{"Content-Type":"application/json"}),...init?.headers}});
  if(response.status===401){throw new Error("AUTH_REQUIRED")}
  if(!response.ok){const body=await response.json().catch(()=>({detail:"Request failed"}));throw new Error(body.detail??"Request failed")}
  return response.status===204?undefined as T:response.json();
}

export function inr(value:number|string){
  return new Intl.NumberFormat("en-IN",{style:"currency",currency:"INR",maximumFractionDigits:2}).format(Number(value));
}
