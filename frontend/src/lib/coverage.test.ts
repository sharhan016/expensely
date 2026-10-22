import { describe, expect, it } from "vitest";
import { KIND_LABELS, rangeLabel, segmentPercents } from "./coverage";

describe("segmentPercents", () => {
  it("is proportional to days and sums to 100", () => {
    const widths = segmentPercents([{ days: 31 }, { days: 31 }, { days: 62 }]);
    expect(widths.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 6);
    expect(widths[2]).toBeCloseTo(widths[0] * 2, 6);
  });
  it("keeps a one-day marker visible without breaking the total", () => {
    const widths = segmentPercents([{ days: 365 }, { days: 1 }, { days: 365 }]);
    expect(widths[1]).toBe(3);
    expect(widths.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 6);
  });
  it("returns nothing for no segments", () => {
    expect(segmentPercents([])).toEqual([]);
  });
});

describe("labels", () => {
  it("describes a range or a single day in plain words", () => {
    expect(rangeLabel("2026-04-01", "2026-04-04")).toBe("1 Apr 2026 to 4 Apr 2026");
    expect(rangeLabel("2026-04-01", "2026-04-01")).toBe("1 Apr 2026");
  });
  it("labels bridged gaps as their own state, never plain covered", () => {
    expect(KIND_LABELS.bridged).not.toBe(KIND_LABELS.covered);
    expect(KIND_LABELS.bridged).toContain("no statement rows");
  });
});
