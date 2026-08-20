import { expect, type Page } from "@playwright/test";

const USER = process.env.E2E_USER ?? "dev";
const PASS = process.env.E2E_PASS ?? "devpassword";

/** Open the production webapp entry (main.webapp.tsx + real AuthGate). */
export async function openWebapp(page: Page): Promise<void> {
  await page.goto("/index.webapp.html", { waitUntil: "domcontentloaded" });
}

/** Land on the webapp shell and finish AuthGate (login form or already authed). */
export async function ensureAuthed(page: Page): Promise<void> {
  const userBox = page.getByPlaceholder("邮箱或用户名");
  const composer = page.getByPlaceholder(/输入消息/);
  const outage = page.getByText("服务暂时不可用");
  await Promise.race([
    userBox.waitFor({ state: "visible", timeout: 30_000 }),
    composer.waitFor({ state: "visible", timeout: 30_000 }),
    outage.waitFor({ state: "visible", timeout: 30_000 }),
  ]);
  if (await outage.isVisible().catch(() => false)) {
    throw new Error(
      "AuthGate shows outage — mock API unreachable from the webapp (check VITE_API_URL)",
    );
  }
  if (await userBox.isVisible().catch(() => false)) {
    await userBox.fill(USER);
    await page.getByPlaceholder(/密码/).first().fill(PASS);
    await page.locator('button[type="submit"]').click();
  }
  await composer.waitFor({ state: "visible", timeout: 30_000 });
}

export async function sendPrompt(
  page: Page,
  text: string,
): Promise<void> {
  const composer = page.getByPlaceholder(/输入消息/);
  await composer.click();
  await composer.fill(text);
  await page.getByRole("button", { name: "发送" }).click();
}

/** Wait until the turn leaves the streaming chrome (停止生成 hidden). */
export async function waitTurnSettled(
  page: Page,
  timeoutMs = 60_000,
): Promise<void> {
  const stop = page.getByRole("button", { name: "停止生成" });
  // May appear briefly; tolerate already-finished turns.
  await stop.waitFor({ state: "visible", timeout: 15_000 }).catch(() => {});
  await stop.waitFor({ state: "hidden", timeout: timeoutMs });
}

export async function expectHashConversation(page: Page): Promise<string> {
  await expect
    .poll(() => page.evaluate(() => window.location.hash), { timeout: 20_000 })
    .toMatch(/#\/conversations\/[a-f0-9]+/);
  const hash = await page.evaluate(() => window.location.hash);
  const m = /#\/conversations\/([a-f0-9]+)/.exec(hash);
  expect(m?.[1]).toBeTruthy();
  return m![1];
}

export function scriptPrompt(script: string, label: string): string {
  return `${label} __e2e_script__:${script}`;
}
