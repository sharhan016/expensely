import { request, type FullConfig } from "@playwright/test";
import { AUTH_STATE, ensureAuthDirectory, loadCredentials } from "./credentials";

// Creates the shared account if the (disposable) test database does not have it yet, signs in,
// and saves the session so signed-in specs start authenticated.
export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL ?? "http://127.0.0.1:3100";
  const { email, password, name } = loadCredentials();
  ensureAuthDirectory();
  const context = await request.newContext({ baseURL });
  const registered = await context.post("/api/auth/register", {
    data: { email, password, display_name: name },
  });
  if (!registered.ok()) {
    if (registered.status() !== 409) {
      throw new Error(`E2E account could not be created (HTTP ${registered.status()})`);
    }
    const login = await context.post("/api/auth/login", { data: { email, password } });
    if (!login.ok()) {
      throw new Error(
        `E2E account exists but the stored credentials were rejected (HTTP ${login.status()}). ` +
          "Reset the test database or fix .env.e2e.",
      );
    }
  }
  await context.storageState({ path: AUTH_STATE });
  await context.dispose();
}
