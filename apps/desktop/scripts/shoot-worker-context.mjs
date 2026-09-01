// One-off verification: worker run-detail structured「收到的上下文」.
// Preview chat does not mount SidePanel — `?zoom=graph` navigates to the
// turn-detail page (which does) so showRunDetail has a dock to render into.
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const outDir = resolve(desktopDir, "shoot-out");
const SCENARIO = "multi_agent_received_context";
const SETTLE_MS = 1800;

async function boot() {
  process.chdir(desktopDir);
  await mkdir(outDir, { recursive: true });
  const server = await createServer({
    configFile: resolve(desktopDir, "vite.web.config.ts"),
    logLevel: "warn",
  });
  await server.listen();
  const base = server.resolvedUrls?.local?.[0];
  if (!base) {
    await server.close();
    throw new Error("Vite did not report a local URL.");
  }
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "light",
  });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("agentcore:theme", "light");
      localStorage.setItem("agentcore:side-panel-open", "false");
    } catch {
      /* ignore */
    }
  });
  return { server, browser, page, base };
}

async function openScenario(page, base, scenario) {
  const url = new URL("index.web.html", base);
  url.searchParams.set("shoot", String(Date.now()));
  url.hash = `/preview?s=${encodeURIComponent(scenario)}&zoom=graph`;
  await page.goto(url.href, { waitUntil: "load", timeout: 30_000 });
  await page.waitForSelector(
    `[data-preview-scenario="${scenario}"][data-preview-frame="full"]`,
    { timeout: 15_000 },
  );
  await page.waitForURL(/#\/conversations\/preview-.*\/turn\//, {
    timeout: 10_000,
  });
  await page.waitForTimeout(SETTLE_MS);
}

async function openWorkerAndExpandContext(page) {
  // GraphView nodes carry aria-label like「研究员，已完成…」
  await page
    .getByRole("button", { name: /^研究员，/ })
    .first()
    .click({ timeout: 5000 });
  await page.waitForTimeout(800);

  const detailCtx = page.getByRole("button", {
    name: /收到的上下文/,
  });
  const count = await detailCtx.count();
  if (count === 0) throw new Error("No「收到的上下文」button found");
  await detailCtx.nth(count - 1).click({ timeout: 5000 });
  await page.waitForTimeout(400);

  const blockButtons = page
    .getByRole("button")
    .filter({ hasText: /原始用户请求|你的任务|依赖|团队简报|历史/ });
  if (await blockButtons.count()) {
    await blockButtons.first().click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(300);
  }
}

async function dumpContextSection(page) {
  return page.evaluate(() => {
    const buttons = [...document.querySelectorAll("button,[role=button]")];
    const ctx = [...buttons].reverse().find((b) => {
      const t = (b.innerText || "").replace(/\s+/g, " ").trim();
      return (
        t.startsWith("收到的上下文") && !/multi_agent|single_agent|CEO/.test(t)
      );
    });
    return ctx?.closest("section")?.innerText?.slice(0, 1500) ?? "(no section)";
  });
}

async function main() {
  const { server, browser, page, base } = await boot();
  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));

  try {
    await openScenario(page, base, SCENARIO);
    await openWorkerAndExpandContext(page);

    const structuredPath = resolve(outDir, "worker_context_structured.png");
    await page.screenshot({ path: structuredPath });
    console.log(`Wrote ${structuredPath}`);
    console.log("--- structured section text ---");
    console.log(await dumpContextSection(page));
  } finally {
    await browser.close();
    await server.close();
  }

  if (pageErrors.length) {
    console.error("page errors:", pageErrors.join(" | "));
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
