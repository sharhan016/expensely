import { expect, test } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { RESTART_MARKER } from "./credentials";

// Phone-width journey: first visit, coming back, a gap question, "not needed", a real server
// restart in the middle of a question, and the Records page.
test.use({ viewport: { width: 390, height: 844 } });

const header =
  "YES BANK Statement,,,,\nAccount Type: Current,,,,\nAccount Number: 0012XXXX7788,,,,\n" +
  "Date,Narration,Withdrawal,Deposit,Closing Balance\n";
const row = (date: string, debit: string, balance: string) =>
  `${date},UPI/DR/001/SHOP/YESB/shop@okaxis/SYNTHETIC SHOP,${debit},,${balance}\n`;
const noSideScroll = async (page: import("@playwright/test").Page) =>
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

async function confirmUpload(page: import("@playwright/test").Page, name: string, body: string) {
  await page.goto("/imports");
  await page.locator('input[type="file"]').setInputFiles({ name, mimeType: "text/csv", buffer: Buffer.from(body) });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  await expect(page.getByText("Extraction result")).toBeVisible();
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(/added to the ledger/)).toBeVisible();
}

async function uploadToAssistant(page: import("@playwright/test").Page, name: string, body: string) {
  await page.goto("/assistant");
  await page.getByLabel("Statement files").setInputFiles({ name, mimeType: "text/csv", buffer: Buffer.from(body) });
  await page.getByLabel("Message your financial assistant").fill("Please read this statement");
  await page.getByLabel("Send message").click();
}

test("first visit, return with a gap, not needed, server restart and records at 390px", async ({ page, request }) => {
  const email = `workflow-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Workflow Test");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  // 1. First visit: the assistant asks for the period and states its limits.
  await page.goto("/assistant");
  await expect(page.getByTestId("question")).toContainText("Which period do you want to understand?");
  await noSideScroll(page);
  await page.getByLabel("First date").fill("2026-01-01");
  await page.getByLabel("Last date").fill("2026-06-30");
  await page.getByRole("button", { name: "Save period" }).click();
  await expect(page.getByText(/cannot tell what happened on dates with no statement/)).toBeVisible();

  // 2. Coming back: it knows where everything is reconciled and what to send.
  await confirmUpload(page, "jan.csv", header + row("01/01/2026", "100.00", "900.00") + row("20/01/2026", "100.00", "800.00"));
  await page.goto("/assistant");
  await expect(page.getByTestId("greeting")).toContainText("reconciled through 20 Jan 2026");
  await expect(page.getByTestId("greeting")).toContainText("Send me anything after 20 Jan 2026");
  await expect(page.getByTestId("greeting")).toContainText("Credit cards are not checked yet");
  await noSideScroll(page);

  // 3. A later statement that does not connect: one focused question; "not needed" is recorded.
  await uploadToAssistant(page, "mar.csv", header + row("10/03/2026", "50.00", "700.00"));
  await expect(page.getByTestId("question")).toContainText("Nothing covers 21 Jan 2026 to 9 Mar 2026", { timeout: 30_000 });
  await noSideScroll(page);
  await page.getByRole("button", { name: "That period is not needed" }).click();
  await expect(page.getByText("Noted your answer about those dates.")).toBeVisible();
  await expect(page.getByTestId("question")).toHaveCount(0);
  await page.getByRole("link", { name: "Review statement" }).last().click();
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(/added to the ledger/)).toBeVisible();

  // 4. Records at phone width: acknowledged is its own state and never counts.
  await page.goto("/records");
  await expect(page.getByText("You said these dates are not needed; they are not counted as checked")).toBeVisible();
  await expect(page.getByTestId("overall-summary")).toContainText("reconciled through 20 Jan 2026");
  await noSideScroll(page);

  // 5. A real server restart in the middle of a question: it resumes from stored state.
  await uploadToAssistant(page, "jun.csv", header + row("10/06/2026", "25.00", "600.00"));
  await expect(page.getByTestId("question")).toContainText("Nothing covers 11 Mar 2026 to 9 Jun 2026", { timeout: 30_000 });
  mkdirSync(path.dirname(RESTART_MARKER), { recursive: true });
  writeFileSync(RESTART_MARKER, "restart");
  await expect.poll(() => existsSync(RESTART_MARKER), { timeout: 10_000 }).toBe(false);
  await expect.poll(async () => (await request.get("/api/health").catch(() => null))?.ok() ?? false, { timeout: 60_000, intervals: [500] }).toBe(true);
  await page.reload();
  await expect(page.getByTestId("question")).toContainText("Nothing covers 11 Mar 2026 to 9 Jun 2026");
  await page.getByRole("button", { name: "I’ll upload that statement" }).click();
  await expect(page.getByText("Noted your answer about those dates.").last()).toBeVisible();
  await expect(page.getByTestId("question")).toHaveCount(0);
});

async function registerFresh(page: import("@playwright/test").Page, label: string) {
  const email = `${label}-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Phone Test");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");
}

test("asking where an amount went shows an answer card and opens the evidence at 390px", async ({ page }) => {
  await registerFresh(page, "ask");
  await confirmUpload(
    page,
    "jan-ask.csv",
    header +
      row("05/01/2026", "1200.00", "8800.00") +
      row("10/01/2026", "500.00", "8300.00").replace("UPI/DR/001/SHOP/YESB/shop@okaxis/SYNTHETIC SHOP", "ATM WDL SYNTHETIC BRANCH") +
      row("12/01/2026", "700.00", "7600.00").replace("UPI/DR/001/SHOP/YESB/shop@okaxis/SYNTHETIC SHOP", "ATM WDL SYNTHETIC BRANCH"),
  );

  await page.goto("/assistant");
  await page.getByLabel("Message your financial assistant").fill("where did my 1200 go");
  await page.getByLabel("Send message").click();
  const card = page.getByRole("group", { name: "What I found" });
  await expect(card).toContainText("I found 1 movement of exactly ₹1,200.00: money out on 5 Jan 2026.", { timeout: 30_000 });
  await expect(card).toContainText("Possible, not a cause: ATM withdrawals of ₹500.00 on 10 Jan 2026 and ₹700.00 on 12 Jan 2026 add up to ₹1,200.00.");
  await expect(card).toContainText("I cannot tell from the records why money moved.");
  await noSideScroll(page);

  await card.getByRole("button", { name: "Open the 1 record" }).first().click();
  const evidence = card.getByRole("table", { name: "Evidence rows" });
  await expect(evidence).toContainText("SYNTHETIC SHOP");
  await expect(evidence).toContainText("1,200.00");
  await noSideScroll(page);

  await page.getByLabel("Message your financial assistant").fill("where did my 999 go");
  await page.getByLabel("Send message").click();
  await expect(page.getByText("I found nothing in my records that matches ₹999.00.")).toBeVisible();
  await expect(page.getByText("Roughly when did it happen? Even a month helps.").last()).toBeVisible();
});

test("the report page works at 390px and asks for statements when a range is empty", async ({ page }) => {
  await registerFresh(page, "report390");
  await confirmUpload(page, "jan-report.csv", header + row("05/01/2026", "100.00", "900.00") + row("10/01/2026", "50.00", "850.00"));

  await page.goto("/report");
  await page.getByLabel("From", { exact: true }).fill("2026-01-01");
  await page.getByLabel("To", { exact: true }).fill("2026-01-31");
  await page.getByRole("button", { name: "Show report" }).click();
  await expect(page.getByTestId("report-summary")).toContainText("2 movements");
  await noSideScroll(page);
  await page.getByRole("button", { name: "Not yet explained" }).click();
  await expect(page.getByRole("table", { name: "Rows" })).toContainText("SYNTHETIC SHOP");
  await expect(page.getByText("Showing all 2")).toBeVisible();
  await noSideScroll(page);

  await page.getByLabel("From", { exact: true }).fill("2025-06-01");
  await page.getByLabel("To", { exact: true }).fill("2025-06-30");
  await page.getByRole("button", { name: "Show report" }).click();
  await expect(page.getByTestId("report-summary")).toContainText("Send me the statements that cover these dates");
  await expect(page.getByRole("button", { name: "Not yet explained" })).toHaveCount(0); // no zero tables
  await noSideScroll(page);

  await page.goto("/");
  await expect(page.getByTestId("period-summary")).toBeVisible();
});

test("money sent to a person is unknown until she says, then tracked and settled at 390px", async ({ page }) => {
  await registerFresh(page, "motion");
  const debit = (date: string, amount: string, balance: string) =>
    `${date},UPI/DR/001/RAVI/YESB/ravi@okaxis/SYNTHETIC LUNCH,${amount},,${balance}\n`;
  await confirmUpload(
    page,
    "jan-motion.csv",
    header +
      debit("05/01/2026", "200.00", "800.00") +
      debit("10/01/2026", "300.00", "500.00") +
      "20/01/2026,UPI/CR/009/RAVI/YESB/ravi@okaxis/SYNTHETIC BACK,,100.00,600.00\n",
  );

  // Default: nothing is assumed. The report shows the payments as not yet explained.
  await page.goto("/report");
  await page.getByLabel("From", { exact: true }).fill("2026-01-01");
  await page.getByLabel("To", { exact: true }).fill("2026-01-31");
  await page.getByRole("button", { name: "Show report" }).click();
  await expect(page.getByRole("button", { name: "Not yet explained" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sent out, expected back" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Marked as spending" })).toHaveCount(0);

  // One question for the person, not one per payment.
  await page.goto("/assistant");
  const question = page.getByRole("group", { name: /Question about/ });
  await expect(question).toContainText("You sent 2 payments totalling ₹500.00", { timeout: 30_000 });
  await noSideScroll(page);
  await question.getByRole("button", { name: "I expect it back" }).click();
  await expect(page.getByRole("list", { name: "Still out" })).toContainText("₹500.00 still out of ₹500.00 since 5 Jan 2026");

  // The incoming payment is only a proposal until she confirms it; it is never income.
  const proposal = page.getByRole("group", { name: /Possible return from/ });
  await expect(proposal).toContainText("₹100.00 came in from");
  await proposal.getByRole("button", { name: "Yes, it came back" }).click();
  await expect(page.getByRole("list", { name: "Still out" })).toContainText("₹400.00 still out of ₹500.00");
  await expect(page.getByRole("list", { name: "Still out" })).toContainText("₹100.00 has come back");
  await noSideScroll(page);

  await page.getByLabel("Message your financial assistant").fill("what is still out?");
  await page.getByLabel("Send message").click();
  await expect(page.getByText(/still out of ₹500.00 sent since 5 Jan 2026, and ₹100.00 has come back/).last()).toBeVisible();

  await page.goto("/report");
  await page.getByLabel("From", { exact: true }).fill("2026-01-01");
  await page.getByLabel("To", { exact: true }).fill("2026-01-31");
  await page.getByRole("button", { name: "Show report" }).click();
  await expect(page.getByTestId("report-summary")).toContainText("Sent out and expected back: ₹500.00 (not counted as spending)");
  await expect(page.getByTestId("report-summary")).toContainText("Returned to you: ₹100.00 (not counted as income)");
  await expect(page.getByRole("button", { name: "Marked as income" })).toHaveCount(0);
  await noSideScroll(page);
});

const debit = (date: string, amount: string, balance: string) =>
  `${date},UPI/DR/001/RAVI/YESB/ravi@okaxis/SYNTHETIC LUNCH,${amount},,${balance}\n`;

test("writing off sent-out money and undoing it at 390px", async ({ page }) => {
  await registerFresh(page, "writeoff");
  await confirmUpload(page, "jan-wo.csv", header + debit("05/01/2026", "200.00", "800.00") + debit("10/01/2026", "300.00", "500.00"));
  await page.goto("/assistant");
  await page.getByRole("group", { name: /Question about/ }).getByRole("button", { name: "I expect it back" }).click({ timeout: 30_000 });
  const still = page.getByRole("list", { name: "Still out" });
  await expect(still).toContainText("₹500.00 still out of ₹500.00");

  await still.getByRole("button", { name: "Write off" }).click();
  await expect(still).toContainText("Written off (not counted)");
  await noSideScroll(page);
  await page.goto("/report");
  await page.getByLabel("From", { exact: true }).fill("2026-01-01");
  await page.getByLabel("To", { exact: true }).fill("2026-01-31");
  await page.getByRole("button", { name: "Show report" }).click();
  await expect(page.getByTestId("report-summary")).toContainText("Written off (not counted): ₹500.00");
  await expect(page.getByTestId("report-summary")).not.toContainText("Sent out and expected back");
  await expect(page.getByRole("button", { name: "Written off (not counted)" })).toBeVisible();

  await page.goto("/assistant");
  await page.getByRole("list", { name: "Still out" }).getByRole("button", { name: "Undo write-off" }).click();
  await expect(page.getByRole("list", { name: "Still out" })).toContainText("₹500.00 still out of ₹500.00");
  await page.goto("/report");
  await page.getByLabel("From", { exact: true }).fill("2026-01-01");
  await page.getByLabel("To", { exact: true }).fill("2026-01-31");
  await page.getByRole("button", { name: "Show report" }).click();
  await expect(page.getByTestId("report-summary")).toContainText("Sent out and expected back: ₹500.00");
  await expect(page.getByTestId("report-summary")).not.toContainText("Written off");
});

test("remembering an answer for a person, seeing it applied, and removing it at 390px", async ({ page }) => {
  await registerFresh(page, "remember");
  await confirmUpload(page, "jan-rem.csv", header + debit("05/01/2026", "200.00", "800.00") + debit("10/01/2026", "300.00", "500.00"));
  await page.goto("/assistant");
  const question = page.getByRole("group", { name: /Question about/ });
  await expect(question).toContainText("You sent 2 payments", { timeout: 30_000 });
  await expect(question.getByLabel("Remember my answer for this person")).not.toBeChecked(); // off by default
  await question.getByLabel("Remember my answer for this person").check();
  await question.getByRole("button", { name: "It was an expense" }).click();
  await expect(page.getByRole("group", { name: /Question about/ })).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Remembered choices" })).toContainText("counted as expenses automatically (0 later payments so far)");

  // A later payment is applied automatically, and disclosed.
  await confirmUpload(page, "feb-rem.csv", header + "10/02/2026,QR PAYMENT ravi@okaxis,100.00,,400.00\n");
  await page.goto("/assistant");
  await expect(page.getByRole("list", { name: "Remembered choices" })).toContainText("(1 later payment so far)");
  await expect(page.getByRole("group", { name: /Question about/ })).toHaveCount(0);
  await noSideScroll(page);

  // Undo: the applied payment becomes a question again and she stops being remembered.
  await page.getByRole("button", { name: "Undo applied and stop remembering" }).click();
  await expect(page.getByRole("list", { name: "Remembered choices" })).toHaveCount(0);
  await expect(page.getByRole("group", { name: /Question about/ })).toContainText("You sent 1 payment totalling ₹100.00");
});

test("a recorded loan is proposed as an expectation, accepted and reverted at 390px", async ({ page }) => {
  await registerFresh(page, "loan");
  await confirmUpload(
    page,
    "jan-loan.csv",
    header + debit("05/01/2026", "200.00", "800.00") + debit("10/01/2026", "300.00", "500.00") +
      "20/01/2026,UPI/CR/009/RAVI/YESB/ravi@okaxis/SYNTHETIC BACK,,100.00,600.00\n",
  );
  // Record them as loans to a person (the existing way to classify), through the signed-in API.
  const person = await (await page.request.post("/api/people", { data: { name: "Meera Synthetic" } })).json();
  const listed = await (await page.request.get("/api/transactions?limit=50")).json();
  for (const item of listed.items) {
    const purpose = Number(item.amount) < 0 ? "loan_given" : "loan_repayment";
    const response = await page.request.patch(`/api/transactions/${item.id}`, {
      data: { purpose, person_id: person.id, expected_version: item.meaning_version },
    });
    expect(response.ok()).toBe(true);
  }

  await page.goto("/assistant");
  const proposal = page.getByRole("group", { name: "Loans to Meera Synthetic" });
  await expect(proposal).toContainText("2 loans totalling ₹500.00", { timeout: 30_000 });
  await expect(proposal).toContainText("1 repayment totalling ₹100.00");
  await expect(proposal).toContainText("Nothing changes until you say yes");
  await noSideScroll(page);

  await proposal.getByRole("button", { name: "Yes, track them" }).click();
  const still = page.getByRole("list", { name: "Still out" });
  await expect(still).toContainText("Meera Synthetic");
  await expect(still).toContainText("₹400.00 still out of ₹500.00");
  await expect(still).toContainText("from your recorded loans");
  await expect(page.getByRole("group", { name: "Loans to Meera Synthetic" })).toHaveCount(0);

  await still.getByRole("button", { name: "Undo mapping" }).click();
  await expect(page.getByRole("list", { name: "Still out" })).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Loans to Meera Synthetic" })).toBeVisible();

  await page.goto("/report");
  await page.getByLabel("From", { exact: true }).fill("2026-01-01");
  await page.getByLabel("To", { exact: true }).fill("2026-01-31");
  await page.getByRole("button", { name: "Show report" }).click();
  await expect(page.getByTestId("obligation-note")).toContainText("Entrusted money and reimbursements stay in their own group");
  await noSideScroll(page);
});
