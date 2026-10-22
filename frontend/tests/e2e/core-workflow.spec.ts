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
        "Previous Balance : Rs. 1,000.00 Dr\n" +
        "Statement Balance: Rs. 500.00 Dr\n" +
        "Total Amount Due: Rs. 450.00\n" +
        "Minimum Amount Due: Rs. 90.00\n" +
        "Available Credit: Rs. 49,500.00\n" +
        "Statement Details\n" +
        "Date Transaction Details Merchant Category Amount (Rs.)\n" +
        "01/04/2026 AMAZON MARKETPLACE Shopping 750.00 Dr\n" +
        "03/04/2026 AMAZON REFUND Shopping 750.00 Cr\n" +
        "05/04/2026 PAYMENT RECEIVED - Ref No: PAY500 Payments 500.00 Cr\n",
    ),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  await expect(page.getByText("Credit card detected")).toBeVisible();
  await expect(page.getByText("Total amount due")).toBeVisible();
  await expect(page.getByText("₹450.00", { exact: true })).toBeVisible();
  await expect(page.getByText("Minimum amount due")).toBeVisible();
  await expect(page.getByText("Available credit")).toBeVisible();
  await expect(page.getByText("Statement balances reconcile")).toBeVisible();
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
  const refundLink = page.locator(".refund-link-panel article").filter({ hasText: "refund" });
  await expect(refundLink).toContainText("₹750");
  await expect(page.getByRole("button", { name: /Amazon Marketplace/ })).toBeVisible();
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

test("pages a long ledger honestly and dates every balance", async ({ page }) => {
  const email = `paging-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Paging Test");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();

  const rows = 205;
  const start = Date.UTC(2026, 0, 1);
  const lines = ["HDFC Bank Statement,,,,", "Date,Narration,Withdrawal,Deposit,Closing Balance"];
  for (let index = 0; index < rows; index += 1) {
    const day = new Date(start + index * 86_400_000);
    const stamp = `${String(day.getUTCDate()).padStart(2, "0")}/${String(day.getUTCMonth() + 1).padStart(2, "0")}/${day.getUTCFullYear()}`;
    lines.push(`${stamp},UPI SYNTHETIC ROW ${index},10.00,,${(1_000_000 - 10 * (index + 1)).toFixed(2)}`);
  }
  const lastDay = new Date(start + (rows - 1) * 86_400_000);
  const lastLabel = `${lastDay.getUTCDate()} ${lastDay.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })} ${lastDay.getUTCFullYear()}`;

  await page.getByRole("link", { name: "Accounts & Import" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "hdfc-long.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(lines.join("\n") + "\n"),
  });
  await page.getByRole("button", { name: "Extract transactions" }).click();
  await expect(page.getByText("Extraction result")).toBeVisible();
  await page.getByRole("button", { name: /Confirm to ledger/ }).click();
  await expect(page.getByText(new RegExp(`${rows} transactions added to the ledger`))).toBeVisible();

  await page.getByRole("link", { name: "Transactions" }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Showing 200 of 205/ })).toBeVisible();
  await expect(page.getByText("UPI SYNTHETIC ROW 204")).toBeVisible();
  await expect(page.getByText("UPI SYNTHETIC ROW 0", { exact: true })).toHaveCount(0);
  // Newest row's balance is the exact backend figure, formatted only.
  await expect(page.getByRole("row").filter({ hasText: "UPI SYNTHETIC ROW 204" })).toContainText("₹9,97,950.00");

  await page.getByRole("button", { name: "Show more" }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Showing all 205/ })).toBeVisible();
  await expect(page.getByText("UPI SYNTHETIC ROW 0", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Show more" })).toHaveCount(0);

  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: `As of ${lastLabel}` })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "not today's balance" })).toBeVisible();
});

test("shows what my records cover, bridges a gap visibly and tracks cash", async ({ page }) => {
  const email = `records-${Date.now()}-${Math.random().toString(16).slice(2)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "Create a private workspace" }).click();
  await page.getByLabel("Your name").fill("Records Test");
  await page.getByLabel("Email").fill(email);
  await page.locator('input[name="password"]').fill("local-test-password");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page).toHaveURL("/");

  const header =
    "YES BANK Statement,,,,\nAccount Type: Current,,,,\nAccount Number: 0012XXXX7788,,,,\n" +
    "Date,Narration,Withdrawal,Deposit,Closing Balance\n";
  const statements = [
    {
      name: "yes-jan.csv",
      body:
        header +
        "01/01/2026,UPI/DR/001/SHOP/YESB/shop@okaxis/SYNTHETIC SHOP,100.00,,900.00\n" +
        "05/01/2026,ATM WDL SYNTHETIC BRANCH,300.00,,600.00\n" +
        "20/01/2026,UPI/DR/002/SHOP/YESB/shop@okaxis/SYNTHETIC SHOP,100.00,,500.00\n",
    },
    {
      // Opens at 500.00, exactly where January closed, but starts weeks later.
      name: "yes-mar.csv",
      body: header + "10/03/2026,UPI/DR/003/SHOP/YESB/shop@okaxis/SYNTHETIC SHOP,50.00,,450.00\n",
    },
  ];
  for (const statement of statements) {
    await page.goto("/imports");
    await page.locator('input[type="file"]').setInputFiles({
      name: statement.name,
      mimeType: "text/csv",
      buffer: Buffer.from(statement.body),
    });
    await page.getByRole("button", { name: "Extract transactions" }).click();
    await expect(page.getByText("Extraction result")).toBeVisible();
    await page.getByRole("button", { name: /Confirm to ledger/ }).click();
    await expect(page.getByText(/added to the ledger/)).toBeVisible();
  }

  await page.getByRole("link", { name: "Records" }).click();
  await expect(page.getByRole("heading", { name: "Records and coverage" })).toBeVisible();
  const overall = page.getByTestId("overall-summary");
  await expect(overall).toContainText("reconciled through 10 Mar 2026");
  await expect(page.getByText("Bridged: balances connect, but no statement rows for these dates")).toBeVisible();
  await expect(page.getByText("21 Jan 2026 to 9 Mar 2026")).toBeVisible();

  await page.getByRole("button", { name: "Un-bridge" }).click();
  await expect(overall).toContainText("reconciled through 20 Jan 2026");
  await expect(page.getByText("Not covered").first()).toBeVisible();
  await page.getByRole("button", { name: "Bridge again" }).click();
  await expect(overall).toContainText("reconciled through 10 Mar 2026");

  await expect(page.getByTestId("cash-summary")).toContainText("Withdrawn, not yet explained: ₹300.00 since 5 Jan 2026");
  await expect(page.getByText(/not a cash balance/)).toBeVisible();
  await page.getByLabel("Date", { exact: true }).fill("2026-01-12");
  await page.getByLabel("Amount", { exact: true }).fill("120.00");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByTestId("cash-summary")).toContainText("₹180.00");
  await expect(page.getByText(/No statement behind this/)).toBeVisible();
  // Cash entries never move the reconciled-through date.
  await expect(overall).toContainText("reconciled through 10 Mar 2026");

  await page.goto("/");
  await expect(page.getByTestId("coverage-banner")).toContainText("reconciled through 10 Mar 2026");
});
