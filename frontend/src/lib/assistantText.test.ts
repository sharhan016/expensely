import { describe, expect, it } from "vitest";
import { inlineParts, parseAssistantText } from "./assistantText";

describe("parseAssistantText", () => {
  it("turns an import answer into a headline, a findings list and a next step", () => {
    const blocks = parseAssistantText(
      "I found a **YES savings-account statement** with **3 transactions**.\n\n**What I found**\n- Money moving out is **₹1,200.00**.\n- **1 meaning still unresolved**: tell me what this was for.\n\n**Next:** review the statement.",
    );
    expect(blocks.map(block => block.kind)).toEqual(["p", "p", "ul", "p"]);
    const list = blocks[2];
    expect(list.kind === "ul" && list.items).toHaveLength(2);
  });

  it("keeps plain multi-line answers as separate paragraphs", () => {
    expect(parseAssistantText("First line\nSecond line")).toEqual([
      { kind: "p", parts: [{ text: "First line", strong: false }] },
      { kind: "p", parts: [{ text: "Second line", strong: false }] },
    ]);
  });

  it("marks only closed **pairs** as emphasis and never parses markup", () => {
    expect(inlineParts("Paid **₹500.00** to <b>x</b> ** open")).toEqual([
      { text: "Paid ", strong: false },
      { text: "₹500.00", strong: true },
      { text: " to <b>x</b> ** open", strong: false },
    ]);
  });
});
