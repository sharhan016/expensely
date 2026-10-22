import { expect, test } from "@playwright/test";
import { loadCredentials } from "./credentials";
import { buildStatement, inrText, renderPdf, statementHtml, type SyntheticRow } from "./pdf-fixtures";

// Ledger and balance cells format negatives as -₹1,234.00.
const money = (paise: number) => `${paise < 0 ? "-" : ""}₹${inrText(Math.abs(paise))}`;
const run = Date.now().toString(36).toUpperCase();

// Amounts are integer paise. Two rows are identical on purpose: both must survive extraction.
const OPENING = 500_000;
const rows: SyntheticRow[] = [
  { date: "01/04/2026", narration: `GROCER SYNTH ${run}`, debit: 123_450 },
  { date: "02/04/2026", narration: `SALARY SYNTH ${run}`, credit: 4_000_000 },
  { date: "03/04/2026", narration: `CAFE SYNTH ${run}`, debit: 5_000 },
  { date: "03/04/2026", narration: `CAFE SYNTH ${run}`, debit: 5_000 },
  { date: "10/04/2026", narration: `RENT SYNTH ${run}`, debit: 12_345_678 },
  { date: "20/04/2026", narration: `REFUND SYNTH ${run}`, credit: 99_950 },
  { date: "30/04/2026", narration: `ATM WDL SYNTH ${run}`, debit: 200_000 },
];

test.describe("shared signed-in account", () => {
  test("starts signed in using the stored credentials session", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: /financial overview/ })).toBeVisible();
    await expect(page).not.toHaveURL(/login/);
  });

  test("PDF extraction matches the review table, the ledger and the assistant", async ({ page, browser }) => {
    const statement = buildStatement(OPENING, rows);
    // Large amounts need enough opening balance to stay positive; this keeps balances realistic.
    expect(statement.closing).toBeGreaterThan(-100_000_000);
    const pdf = await renderPdf(browser, statementHtml(statement.rows));

    // 1. Upload through the assistant and check its plain-language numbers.
    await page.goto("/assistant");
    await page.getByLabel("Statement files").setInputFiles({ name: "synthetic-statement.pdf", mimeType: "application/pdf", buffer: pdf });
    await page.getByLabel("Message your financial assistant").fill("Please read this statement");
    await page.getByLabel("Send message").click();
    const reply = page.locator(".assistant-message").filter({ hasText: /I found a/ }).last();
    // First request after a cold start compiles the route and parses the PDF.
    await expect(reply).toContainText(`with ${rows.length} transactions`, { timeout: 45_000 });
    await expect(reply).toContainText("opening balance, movements, and closing balance reconcile");
    await expect(reply).toContainText(`Money moving out is ₹${inrText(statement.out)}`);
    await expect(reply).toContainText(`money moving in is ₹${inrText(statement.in)}`);

    // 2. The review table shows exactly what the PDF says, row for row.
    await page.getByRole("link", { name: "Review statement" }).last().click();
    await expect(page.getByText("Extraction result")).toBeVisible();
    const review = page.locator("table.responsive-table tbody tr");
    await expect(review).toHaveCount(rows.length);
    for (const [index, expected] of statement.rows.entries()) {
      const row = review.nth(index);
      const [d, m, y] = expected.date.split("/");
      await expect(row.locator('td[data-label="Date"]')).toHaveText(`${y}-${m}-${d}`);
      await expect(row.locator('td[data-label="Description"]')).toContainText(expected.narration);
      const signed = (expected.credit ?? 0) - (expected.debit ?? 0);
      await expect(row.locator('td[data-label="Amount"]')).toHaveText(`${signed >= 0 ? "+" : "−"}₹${inrText(Math.abs(signed))}`);
    }

    // 3. Confirm, then the ledger carries the same rows with exact backend running balances.
    await page.getByRole("button", { name: /Confirm to ledger/ }).click();
    await expect(page.getByText(new RegExp(`${rows.length} transactions added to the ledger`))).toBeVisible();
    await page.getByRole("link", { name: "Transactions" }).click();
    for (const expected of statement.rows.filter(item => !item.narration.startsWith("CAFE"))) {
      const row = page.getByRole("row").filter({ hasText: expected.narration });
      await expect(row).toHaveCount(1);
      await expect(row.locator('td[data-label="Running balance"]')).toContainText(money(expected.balance));
    }
    const cafes = page.getByRole("row").filter({ hasText: `CAFE SYNTH ${run}` });
    await expect(cafes).toHaveCount(2); // identical rows are never merged
    await expect(cafes.nth(0).locator('td[data-label="Running balance"]')).toContainText(money(statement.rows[3].balance));
    await expect(cafes.nth(1).locator('td[data-label="Running balance"]')).toContainText(money(statement.rows[2].balance));
    await expect(page.getByRole("row").filter({ hasText: `ATM WDL SYNTH ${run}` })).toContainText("Cash withdrawal");
  });

  test("the assistant explains an unreadable PDF plainly and never asks for a password", async ({ page }) => {
    await page.goto("/assistant");
    await page.getByLabel("Statement files").setInputFiles({
      name: "damaged.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 this is not a real document"),
    });
    await page.getByLabel("Message your financial assistant").fill("Please read this");
    await page.getByLabel("Send message").click();
    const reply = page.locator(".assistant-message").filter({ hasText: "I could not read damaged.pdf" }).last();
    await expect(reply).toContainText("We could not read this PDF", { timeout: 30_000 });
    await expect(reply).not.toContainText(/password/i);
  });
});

test.describe("stored credentials", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("the credentials file signs in through the login form", async ({ page }) => {
    const { email, password, name } = loadCredentials();
    await page.goto("/login");
    await page.getByLabel("Email").fill(email);
    await page.locator('input[name="password"]').fill(password);
    await page.getByRole("button", { name: "Open workspace" }).click();
    await expect(page).toHaveURL("/");
    await expect(page.getByRole("heading", { name: new RegExp(`${name.split(" ")[0]}`) })).toBeVisible();
  });
});
