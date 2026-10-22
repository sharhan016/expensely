export async function api<T>(path:string, init?:RequestInit):Promise<T>{
  const response=await fetch(`/api${path}`,{credentials:"include",...init,headers:{...(init?.body instanceof FormData?{}:{"Content-Type":"application/json"}),...init?.headers}});
  if(response.status===401){throw new Error("AUTH_REQUIRED")}
  if(!response.ok){const body=await response.json().catch(()=>({detail:"Request failed"}));throw new Error(body.detail??"Request failed")}
  return response.status===204?undefined as T:response.json();
}

// Money arrives from the backend as exact decimal strings; Intl formats the string as given
// instead of converting it through a floating-point number.
export function inr(value:number|string){
  const exact=typeof value==="string"?(value as `${number}`):value;
  return new Intl.NumberFormat("en-IN",{style:"currency",currency:"INR",minimumFractionDigits:2,maximumFractionDigits:2}).format(exact);
}
