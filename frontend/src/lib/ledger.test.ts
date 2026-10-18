import { describe,expect,it } from "vitest";
import { runningBalances } from "./ledger";

describe("runningBalances",()=>{
  it("keeps each account independent while rows are newest first",()=>{
    const balances=runningBalances(
      [{id:1,opening_balance:"1000"},{id:2,opening_balance:"500"}],
      [
        {id:3,account_id:1,amount:"250"},
        {id:2,account_id:2,amount:"-50"},
        {id:1,account_id:1,amount:"-100"},
      ],
    );
    expect(balances).toEqual({1:900,2:450,3:1150});
  });
});
