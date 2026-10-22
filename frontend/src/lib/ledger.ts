// Display helpers only. Every balance is computed by the backend as an exact decimal string;
// nothing here adds, subtracts or otherwise does arithmetic on money.

export type BalanceContext={as_of:string|null;is_stale:boolean;age_days:number|null;freshness?:string};

const MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

export function dateLabel(iso:string){
  const [year,month,day]=iso.split("-").map(Number);
  return `${day} ${MONTHS[month-1]} ${year}`;
}

export function asOfLabel(context:BalanceContext){
  if(!context.as_of)return "No confirmed statement date, so these figures may not be current";
  const age=context.age_days===null?"":` (${context.age_days} days ago)`;
  const base=`As of ${dateLabel(context.as_of)}, from the statement ending that day${age}`;
  if(context.freshness==="current")return `Up to date. ${base}`;
  if(context.freshness==="old")return `${base}. This is months old, not today's balance; upload newer statements to update it`;
  if(context.is_stale)return `${base}. This is more than a month old, not today's balance; upload newer statements to update it`;
  return base;
}

export function showingLabel(showing:number,total:number){
  return showing>=total?`Showing all ${total}`:`Showing ${showing} of ${total}`;
}
