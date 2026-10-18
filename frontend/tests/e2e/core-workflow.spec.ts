import { expect, test } from "@playwright/test";

test("register, import a statement, and open the ledger", async ({ page }) => {
  const email = `e2e-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;

  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Test User");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();

  await expect(page).toHaveURL("/");
  await expect(page.getByRole("heading", { name: "Test’s financial overview" })).toBeVisible();

  await page.getByRole("link", { name: "Accounts & Import" }).click();
  await expect(page.getByRole("heading", { name: "Bank accounts and statement imports" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "hdfc-e2e.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "HDFC Bank Statement,,,,\n" +
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n" +
        "01/04/2026,UPI WATER,160.00,,99840.00\n" +
        "02/04/2026,SALARY,,50000.00,149840.00\n",
    ),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();

  await expect(page.getByText("Extraction result")).toBeVisible();
  await expect(page.getByText("UPI WATER")).toBeVisible();
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(/2 transactions added to the ledger/)).toBeVisible();

  await page.getByRole("link", { name: "People & Funds" }).click();
  await page.getByRole("button", { name: "Add person" }).click();
  await page.getByLabel("Full name").fill("Alex Morgan");
  await page.getByLabel("Relationship").fill("Friend");
  await page.getByRole("button", { name: "Save person" }).click();
  await expect(page.getByRole("button", { name: /Alex Morgan/ })).toBeVisible();

  await page.getByRole("link", { name: "Transactions" }).click();
  await expect(page.getByRole("heading", { name: "Transaction ledger" })).toBeVisible();
  await expect(page.getByText("UPI WATER")).toBeVisible();
  await page.getByLabel("Purpose for UPI WATER").selectOption("loan_given");
  await expect(page.getByLabel("Related person for UPI WATER")).toHaveValue(/\d+/);
  await expect(page.getByLabel("Related person for UPI WATER").locator("option:checked")).toHaveText(
    "Alex Morgan",
  );
  await page
    .getByRole("row")
    .filter({ hasText: "UPI WATER" })
    .getByRole("button", { name: "View history" })
    .click();
  await expect(page.getByText("Meaning history")).toBeVisible();
  await expect(page.getByText(/other → loan given/i)).toBeVisible();

  await page.getByRole("link", { name: "People & Funds" }).click();
  await page.getByRole("button", { name: /Alex Morgan/ }).click();
  await expect(page.getByRole("heading", { name: "Alex Morgan" })).toBeVisible();
  await expect(page.getByText("UPI WATER")).toBeVisible();
});

test("requires bank identification before confirming an unknown statement", async ({ page }) => {
  const email = `bank-check-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Bank Check");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByRole("link", { name: "Accounts & Import" }).click();

  await page.locator('input[type="file"]').setInputFiles({
    name: "unidentified-statement.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "Account Statement,,,,\n" +
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n" +
        "01/04/2026,UPI SHOP,100.00,,900.00\n",
    ),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  await expect(page.getByText("Bank needs confirmation").first()).toBeVisible();

  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText("Choose the bank before adding this statement")).toBeVisible();

  await page.getByLabel("Statement bank").selectOption("yes");
  await page.getByPlaceholder("Name this account").fill("YES savings");
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(/1 transaction added to the ledger/)).toBeVisible();
  await expect(page.getByText("YES savings")).toBeVisible();
});
