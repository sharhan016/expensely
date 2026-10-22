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

test("groups repeated UPI flows and reuses a confirmed meaning", async ({ page }) => {
  const email = `flows-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Flow Test");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByRole("link", { name: "Accounts & Import" }).click();

  await page.locator('input[type="file"]').setInputFiles({
    name: "yes-current.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "YES BANK Statement,,,,\n" +
        "Account Type: Current,,,,\n" +
        "Account Number: 0012XXXX9405,,,,\n" +
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n" +
        "01/04/2026,UPI/DR/001/ASHA/YESB/asha.pay@okaxis/ONE,100.00,,900.00\n" +
        "02/04/2026,UPI/DR/002/ASHA/YESB/asha.pay@okaxis/TWO,200.00,,700.00\n" +
        "03/04/2026,UPI/DR/003/ASHA/YESB/asha.pay@okaxis/THREE,300.00,,400.00\n",
    ),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  await expect(page.getByText("Flow: Asha Pay").first()).toBeVisible();
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(/3 transactions added to the ledger/)).toBeVisible();

  await page.getByRole("link", { name: "Flows" }).click();
  await expect(page.getByRole("heading", { name: "Who money moves to and from" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Asha Pay.*3 movements/ })).toBeVisible();
  await expect(page.getByText("₹600").first()).toBeVisible();
  await page.getByLabel("Display name").fill("Asha");
  await page.getByLabel("Type").selectOption("person");
  await page.getByLabel("Use next time").selectOption("personal");
  await page.getByRole("button", { name: "Confirm & save" }).click();
  await expect(page.getByText("Confirmed identity")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Apply personal to 3 unresolved movements?" }),
  ).toBeVisible();
  await expect(page.getByText(/₹600.*Raw bank descriptions will not change/)).toBeVisible();
  await page.getByRole("button", { name: "Apply to past movements" }).click();
  await expect(page.getByRole("heading", { name: "Past movements updated" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Undo batch change" })).toBeVisible();
  await expect(page.getByText("personal", { exact: true }).first()).toBeVisible();
  if (process.env.CAPTURE_FLOW_UI === "1") {
    await page.screenshot({ path: "/private/tmp/expense-flow-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 360, height: 800 });
    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "/private/tmp/expense-flow-mobile.png", fullPage: true });
    await page.setViewportSize({ width: 1280, height: 720 });
  }

  await page.getByRole("link", { name: "Accounts & Import" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "yes-current-next.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "YES BANK Statement,,,,\n" +
        "Account Type: Current,,,,\n" +
        "Account Number: 0012XXXX9405,,,,\n" +
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n" +
        "01/05/2026,UPI/DR/004/ASHA/YESB/asha.pay@okaxis/FOUR,250.00,,150.00\n",
    ),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  const learnedRow = page.getByRole("row").filter({ hasText: "FOUR" });
  await expect(learnedRow.getByText("Flow: Asha")).toBeVisible();
  await expect(learnedRow.getByText("personal", { exact: true })).toBeVisible();
  await expect(learnedRow.getByText("Confident match")).toBeVisible();
});

test("links a bank debit to its credit-card payment without double counting", async ({ page }) => {
  const email = `card-link-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Card Link Test");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByRole("link", { name: "Accounts & Import" }).click();

  await page.locator('input[type="file"]').setInputFiles({
    name: "yes-current-card-payment.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "YES BANK Statement,,,,\n" +
        "Account Type: Current,,,,\n" +
        "Account Number: 0012XXXX1122,,,,\n" +
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n" +
        "04/04/2026,YES BANK CREDIT CARD PAYMENT 9405,500.00,,500.00\n",
    ),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(/1 transaction added to the ledger/)).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: "yes-credit-card.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "Credit Card Statement YES BANK\n" +
        "YES BANK Uni RuPay\n" +
        "Statement for YES BANK Card Number XXXX XXXX XXXX 9405\n" +
        "Statement Period: 01/04/2026 To 30/04/2026\n" +
        "Statement Details\n" +
        "Date Transaction Details Merchant Category Amount (Rs.)\n" +
        "01/04/2026 AMAZON MARKETPLACE Shopping 750.00 Dr\n" +
        "03/04/2026 AMAZON REFUND Shopping 750.00 Cr\n" +
        "05/04/2026 PAYMENT RECEIVED - Ref No: PAY500 Payments 500.00 Cr\n",
    ),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  await expect(page.getByText("Credit card detected")).toBeVisible();
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(/1 card payment linked/)).toBeVisible();
  await expect(page.getByText(/1 refund linked to card purchases/)).toBeVisible();

  await page.getByRole("link", { name: "Flows" }).click();
  await expect(page.getByRole("heading", { name: "Card payments" })).toBeVisible();
  let cardPayment = page
    .getByRole("article")
    .filter({ hasText: "YES BANK Current Account" });
  await expect(cardPayment).toContainText("YES BANK Uni RuPay Credit Card");
  await expect(cardPayment).toContainText("₹500");
  await expect(page.getByRole("heading", { name: "Card refunds & reversals" })).toBeVisible();
  const refundLink = page.getByRole("article").filter({ hasText: "refund" });
  await expect(refundLink).toContainText("₹750");
  if (process.env.CAPTURE_FLOW_UI === "1") {
    await page.screenshot({ path: "/private/tmp/card-link-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 360, height: 800 });
    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "/private/tmp/card-link-mobile.png", fullPage: true });
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.reload();
    await page.waitForLoadState("networkidle");
    cardPayment = page
      .getByRole("article")
      .filter({ hasText: "YES BANK Current Account" });
  }
  await cardPayment.getByRole("button", { name: "Not a match" }).click();
  await expect(cardPayment.getByText("Not linked")).toBeVisible();
});

test("starts a statement workflow in the private assistant", async ({ page }) => {
  const email = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Assistant Test");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();

  await page.getByRole("link", { name: "Assistant" }).click();
  await expect(page.getByRole("heading", { name: "What should we sort out?" })).toBeVisible();
  await page.getByLabel("Statement files").setInputFiles({
    name: "yes-assistant-current.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "YES BANK Statement,,,,\n" +
        "Account Type: Current,,,,\n" +
        "Account Number: 0012XXXX7788,,,,\n" +
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n" +
        "01/06/2026,UPI/DR/005/RAVI/YESB/ravi@okaxis/LUNCH,450.00,,550.00\n",
    ),
  });
  await page.getByLabel("Message your financial assistant").fill("Please read this statement");
  await page.getByLabel("Send message").click();

  await expect(page.getByText(/YES current-account statement/)).toBeVisible();
  await expect(page.getByText(/1 transaction/)).toBeVisible();
  if (process.env.CAPTURE_ASSISTANT_UI === "1") {
    await page.screenshot({ path: "/private/tmp/assistant-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 360, height: 800 });
    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: "/private/tmp/assistant-mobile.png", fullPage: true });
    await page.setViewportSize({ width: 1280, height: 720 });
  }
  await page.getByRole("link", { name: "Review statement" }).click();
  await expect(page.getByText("Extraction result")).toBeVisible();
  await expect(page.getByText("LUNCH")).toBeVisible();
  await expect(page.getByText("Flow: Ravi")).toBeVisible();
});
