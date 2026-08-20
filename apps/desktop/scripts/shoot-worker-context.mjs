// One-off verification: worker run-detail WorkerContextSection.
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

  // Detail header: 「收到的上下文」 +「N 段」(or diagnostic summary bits).
  const detailCtx = page.getByRole("button", {
    name: /收到的上下文/,
  });
  // Prefer the side-panel one: last match after ScenarioList rows.
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
    const ctx = [...buttons]
      .reverse()
      .find((b) => {
        const t = (b.innerText || "").replace(/\s+/g, " ").trim();
        return (
          t.startsWith("收到的上下文") && !/multi_agent|single_agent|CEO/.test(t)
        );
      });
    return ctx?.closest("section")?.innerText?.slice(0, 1500) ?? "(no section)";
  });
}

async function toggleDiagnosticViaPalette(page) {
  await page.keyboard.press("Control+K");
  await page.waitForTimeout(400);
  const input = page.getByPlaceholder(/搜索|命令/).first();
  if (await input.count()) {
    await input.fill("诊断模式");
  } else {
    await page.keyboard.type("诊断模式");
  }
  await page.waitForTimeout(500);
  const item = page.getByText(/开发者\s*\/\s*诊断模式/).first();
  await item.click({ timeout: 5000 });
  await page.waitForTimeout(400);
  // Dismiss palette if still open.
  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(200);
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

    await toggleDiagnosticViaPalette(page);
    await page.waitForTimeout(500);

    // Ensure expanded under diagnosticMode without toggling closed.
    // Diagnostic shell (no llm-window data) shows「无法从 journal 重建后续轮次」.
    const expanded = await page.evaluate(() => {
      const buttons = [...document.querySelectorAll("button,[role=button]")];
      const ctx = [...buttons]
        .reverse()
        .find((b) => {
          const t = (b.innerText || "").replace(/\s+/g, " ").trim();
          return (
            t.startsWith("收到的上下文") &&
            !/multi_agent|single_agent|CEO/.test(t)
          );
        });
      if (!ctx) return "missing";
      const section = ctx.closest("section");
      const text = section?.innerText ?? "";
      if (
        text.includes("开场上下文") ||
        text.includes("无法从 journal") ||
        text.includes("系统提示")
      ) {
        return "already-expanded-diag";
      }
      if (text.includes("原始请求") || text.includes("你的任务")) {
        // Still normal-mode expanded — click once to collapse then rely on remount,
        // or leave as-is if diagnosticMode didn't flip the tree.
        return "normal-expanded";
      }
      ctx.click();
      return "clicked-to-expand";
    });
    console.log("diagnostic expand state:", expanded);
    await page.waitForTimeout(500);

    // If still normal mode, force re-open worker so RunDetailBody remounts with diagnosticMode.
    if (expanded === "normal-expanded" || expanded === "missing") {
      await page.keyboard.press("Escape").catch(() => {});
      await openWorkerAndExpandContext(page);
    }

    const diagPath = resolve(outDir, "worker_context_diagnostic_shell.png");
    await page.screenshot({ path: diagPath });
    console.log(`Wrote ${diagPath}`);
    console.log("--- diagnostic section text ---");
    console.log(await dumpContextSection(page));
    console.log(
      "NOTE: full llm-window message skeleton unavailable offline — " +
        "useRunLlmWindow short-circuits when isWebPreview() is true.",
    );
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
