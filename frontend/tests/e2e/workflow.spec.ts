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
