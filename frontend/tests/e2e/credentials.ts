import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

// Credentials for the shared end-to-end account. The file is git-ignored; the password is
// random, created once, and never printed or logged. Keep it out of the repo and out of docs.
export const CREDENTIALS_FILE = path.resolve(process.cwd(), "../.env.e2e");
export const AUTH_STATE = path.resolve(process.cwd(), "tests/e2e/.auth/user.json");

export type Credentials = { email: string; password: string; name: string };

function parse(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (match) values[match[1]] = match[2];
  }
  return values;
}

export function loadCredentials(): Credentials {
  if (!existsSync(CREDENTIALS_FILE)) {
    const created = [
      "# Shared end-to-end test account. Git-ignored. Generated once by the Playwright setup.",
      "E2E_EMAIL=e2e-signed-in@example.com",
      `E2E_PASSWORD=${randomBytes(24).toString("hex")}`,
      "E2E_NAME=E2E Signed In",
      "",
    ].join("\n");
    writeFileSync(CREDENTIALS_FILE, created, { mode: 0o600 });
    chmodSync(CREDENTIALS_FILE, 0o600);
  }
  const values = parse(readFileSync(CREDENTIALS_FILE, "utf8"));
  if (!values.E2E_EMAIL || !values.E2E_PASSWORD) {
    throw new Error(`${CREDENTIALS_FILE} must define E2E_EMAIL and E2E_PASSWORD`);
  }
  return { email: values.E2E_EMAIL, password: values.E2E_PASSWORD, name: values.E2E_NAME ?? "E2E Signed In" };
}

export function ensureAuthDirectory() {
  mkdirSync(path.dirname(AUTH_STATE), { recursive: true });
}

export const RESTART_MARKER = path.resolve(process.cwd(), "tests/e2e/.auth/restart-backend");
