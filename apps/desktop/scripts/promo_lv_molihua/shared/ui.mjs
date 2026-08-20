/** Playwright UI helpers shared across capture commands. */

export async function dismissOnboarding(page) {
  const dialog = page.locator('[aria-label="欢迎使用 AgentCore"]');
  if (!(await dialog.isVisible().catch(() => false))) return false;
  const skip = dialog.getByRole("button", { name: /^跳过$/ });
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
  } else {
    const free = dialog.getByRole("button", { name: /先用免费额度/ });
    if (await free.isVisible().catch(() => false)) await free.click();
  }
  await dialog.waitFor({ state: "hidden", timeout: 15_000 }).catch(() => {});
  await page.waitForTimeout(400);
  return true;
}

export async function ensureDebateRoom(page) {
  const debateTab = page.getByRole("button", { name: /^辩论室$/ });
  if (await debateTab.isVisible().catch(() => false)) {
    await debateTab.click();
    await page.waitForTimeout(700);
    return;
  }
  const open = page.getByRole("button", { name: /打开辩论室/ });
  if (await open.first().isVisible().catch(() => false)) {
    await open.first().click();
    await page.waitForTimeout(1200);
  }
}

export async function expandDebateFullText(page) {
  const expand = page.getByRole("button", { name: /展开全文/ });
  const n = await expand.count();
  for (let j = 0; j < Math.min(n, 16); j++) {
    await expand.nth(j).click().catch(() => {});
  }
  if (n > 0) await page.waitForTimeout(400);
}

/**
 * Login if the login form is shown; wait for composer.
 * @returns {{ userBox, composer }}
 */
export async function loginIfNeeded(page, { user, pass }) {
  const userBox = page.getByPlaceholder("邮箱或用户名");
  const composer = page.getByPlaceholder(/输入消息/);
  await Promise.race([
    userBox.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
    composer.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
  ]);
  if (await userBox.isVisible().catch(() => false)) {
    await userBox.fill(user);
    await page.getByPlaceholder(/密码/).first().fill(pass);
    await page.locator('button[type="submit"]').click();
  }
  await composer.waitFor({ state: "visible", timeout: 30_000 });
  await dismissOnboarding(page);
  return { userBox, composer };
}

/** Content-gate: any / all / strict-any needles. */
export function needlesHit(text, job) {
  if (job.requireAll) return job.needles.every((n) => text.includes(n));
  if (job.requireAnyStrict?.length) {
    return job.requireAnyStrict.some((n) => text.includes(n));
  }
  if (job.anyOf || job.needles) {
    return (job.needles || []).some((n) => text.includes(n));
  }
  return true;
}

export async function scrollNeedle(page, needle) {
  return page.evaluate((n) => {
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walk.nextNode())) {
      if (node.textContent && node.textContent.includes(n)) {
        const el = node.parentElement;
        el?.scrollIntoView({ block: "center", inline: "nearest" });
        return (el?.innerText || node.textContent).slice(0, 260);
      }
    }
    return null;
  }, needle);
}
