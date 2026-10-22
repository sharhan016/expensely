import { expect, test, type Page } from "@playwright/test";
import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

// Local only: skipped when reference-docs is absent. Records a REDACTED transcript for review:
// counterparty names, UPI handles and long digit runs are masked; amounts and app wording stay.
const folder = path.resolve(process.cwd(), "../reference-docs");
const available = existsSync(path.join(folder, "doc-passphrase.md"));
const out = path.resolve(process.cwd(), "test-results/real-ux");
const transcript = path.join(out, "transcript.txt");
test.skip(!available, "reference-docs is not present");
test.setTimeout(240_000);

const passwords = available
  ? readFileSync(path.join(folder, "doc-passphrase.md"), "utf8").split("\n").filter(l => l.includes("=")).map(l => l.split("=").slice(1).join("=").trim())
  : [];
let names: string[] = [];
const redact = (text: string) => {
  let result = text.replace(/\S+@\S+/g, "<handle>").replace(/\d{8,}/g, "<num>");
  for (const name of [...names].sort((a, b) => b.length - a.length)) if (name.length > 2) result = result.split(name).join("<person>");
  return result;
};
const log = (label: string, text: string) => appendFileSync(transcript, `\n## ${label}\n${redact(text)}\n`);
const body = async (page: Page, selector = "main") => (await page.locator(selector).first().innerText()).replace(/\n{2,}/g, "\n");
async function learnNames(page: Page) {
  const list = await (await page.request.get("/api/counterparties")).json().catch(() => []);
  names = Array.isArray(list) ? list.map((c: { display_name: string }) => c.display_name) : [];
}

test("real statements through the test account: conversation, calculations and UX", async ({ page }) => {
  mkdirSync(out, { recursive: true });
  writeFileSync(transcript, "REDACTED TRANSCRIPT (local)\n");
  const pdfs = ["Account_Statement", "PNBONE"].map(prefix => {
    const file = readdirSync(folder).find(f => f.startsWith(prefix) && f.endsWith(".pdf"))!;
    return { name: "statement-" + prefix.toLowerCase() + ".pdf", mimeType: "application/pdf", buffer: readFileSync(path.join(folder, file)) };
  });

  // Phase A: first visit
  await page.goto("/assistant");
  await expect(page.getByTestId("greeting")).toBeVisible();
  log("A1 first visit greeting + question", await body(page, ".assistant-feed"));
  await page.getByLabel("First date").fill("2026-06-01");
  await page.getByLabel("Last date").fill("2026-10-23");
  await page.getByRole("button", { name: "Save period" }).click();
  await expect(page.getByText(/cannot tell what happened on dates with no statement/)).toBeVisible();
  log("A2 after period answered", await body(page, ".assistant-feed"));

  // Phase B: upload both protected PDFs in one message
  await page.getByLabel("Statement files").setInputFiles(pdfs);
  await page.getByLabel("Message your financial assistant").fill("Please read these two statements");
  await page.getByLabel("Send message").click();
  const field = page.getByLabel("PDF password");
  await expect(field).toBeVisible({ timeout: 60_000 });
  log("B1 password question", await body(page, ".workflow-panel"));
  for (let round = 0; round < 4 && (await page.getByLabel("PDF password").count()) > 0; round += 1) {
    let unlocked = false;
    for (const password of passwords) {
      await page.getByLabel("PDF password").fill(password);
      await page.getByRole("button", { name: "Unlock and continue" }).click();
      await page.waitForTimeout(2500);
      if ((await page.getByRole("alert").filter({ hasText: /did not unlock/ }).count()) === 0) { unlocked = true; break; }
      log("B2 wrong passphrase message", await body(page, ".workflow-panel"));
    }
    expect(unlocked).toBe(true);
  }
  await expect(page.locator(".assistant-message.assistant").filter({ hasText: /I found a/ }).first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(1500);
  log("B3 after unlocking (progress events)", await body(page, ".assistant-feed"));

  // Phase C: review and confirm
  await page.goto("/imports");
  await expect(page.getByText("Extraction result")).toBeVisible({ timeout: 30_000 });
  log("C1 import review page", await body(page));
  for (let i = 0; i < 2; i += 1) {
    await page.getByRole("button", { name: /Confirm to ledger/ }).click();
    await page.waitForTimeout(2500);
    log(`C2 after confirm ${i + 1}`, await body(page));
    const next = page.locator(".statement-list button").first();
    if ((await next.count()) > 0 && i === 0) await next.click().catch(() => undefined);
  }
  await learnNames(page);

  // Phase D: returning greeting + grouped questions
  await page.goto("/assistant");
  await page.waitForTimeout(2500);
  await learnNames(page);
  log("D1 returning greeting, questions, still out", await body(page, ".assistant-feed"));
  const questions = page.getByRole("group", { name: /Question about/ });
  log("D2 number of person questions", String(await questions.count()));

  // Phase E: conversation
  const ask = async (message: string) => {
    await page.getByLabel("Message your financial assistant").fill(message);
    await page.getByLabel("Send message").click();
    await page.waitForTimeout(2500);
    const items = page.locator(".assistant-message.assistant");
    log(`E ask: ${message}`, await items.last().innerText());
  };
  const ledger = await (await page.request.get("/api/transactions?limit=200")).json();
  const biggest = [...ledger.items].filter((t: { amount: string }) => Number(t.amount) < 0).sort((a: { amount: string }, b: { amount: string }) => Number(a.amount) - Number(b.amount))[0];
  await ask("what is reconciled?");
  await ask("give me my balance summary");
  await ask("what is still out?");
  await ask("how much cash did I withdraw?");
  await ask(`where did my ${Math.abs(Number(biggest.amount))} go`);
  await ask("where did my 123456 go");
  await ask("I want to understand 1 September to 30 September");
  await ask("yes");
  await ask("what still needs review?");

  // Phase F: pages
  for (const route of ["/records", "/", "/transactions"]) {
    await page.goto(route);
    await page.waitForTimeout(2500);
    log(`F page ${route}`, (await body(page)).slice(0, 2500));
  }
  await page.goto("/report");
  await page.getByLabel("From", { exact: true }).fill("2026-06-01");
  await page.getByLabel("To", { exact: true }).fill("2026-10-23");
  await page.getByRole("button", { name: "Show report" }).click();
  await page.waitForTimeout(2500);
  log("F report", (await body(page)).slice(0, 2500));
  await page.screenshot({ path: path.join(out, "report-desktop.png"), fullPage: true });
});
