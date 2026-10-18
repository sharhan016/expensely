export type BalanceAccount={id:number;opening_balance:string|number};
export type BalanceTransaction={id:number;account_id:number;amount:string|number};

export function runningBalances(accounts:BalanceAccount[],transactions:BalanceTransaction[]){
  const totals:Record<number,number>={};
  accounts.forEach(account=>{totals[account.id]=Number(account.opening_balance)});
  const result:Record<number,number>={};
  [...transactions].reverse().forEach(transaction=>{
    totals[transaction.account_id]=(totals[transaction.account_id]??0)+Number(transaction.amount);
    result[transaction.id]=totals[transaction.account_id];
  });
  return result;
}
