import { describe,expect,it } from "vitest";
import { asOfLabel,dateLabel,showingLabel } from "./ledger";

describe("asOfLabel",()=>{
  it("names the statement date for a current balance",()=>{
    expect(asOfLabel({as_of:"2026-01-31",is_stale:false,age_days:10})).toBe("As of 31 Jan 2026, from the statement ending that day");
  });
  it("says an old balance is not today's balance",()=>{
    const label=asOfLabel({as_of:"2026-01-31",is_stale:true,age_days:120});
    expect(label).toContain("120 days ago");
    expect(label).toContain("not today's balance");
  });
  it("does not imply currency when there is no statement date",()=>{
    expect(asOfLabel({as_of:null,is_stale:true,age_days:null})).toContain("may not be current");
  });
});

describe("showingLabel",()=>{
  it("reports a partial page honestly",()=>{
    expect(showingLabel(200,520)).toBe("Showing 200 of 520");
  });
  it("reports a complete list",()=>{
    expect(showingLabel(3,3)).toBe("Showing all 3");
  });
});

describe("dateLabel",()=>{
  it("formats an ISO date without timezone shifts",()=>{
    expect(dateLabel("2026-03-01")).toBe("1 Mar 2026");
  });
});
