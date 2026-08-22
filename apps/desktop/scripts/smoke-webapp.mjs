// End-to-end smoke for the PRODUCTION web client (form A: desktop renderer in a
// plain browser). Unlike scripts/shoot.mjs (offline #/preview, no backend/auth),
// this boots the REAL web entry (vite.webapp.config.ts → main.webapp.tsx, which
// keeps real cookie auth) and drives a headless Chromium through the full path the
// spike is meant to prove:
//
//   1. boots in a real browser (no Electron preload — browserStubs + __WEB__)
//   2. real auth gate is active (NOT __WEB_PREVIEW__) and the backend is reachable
//      cross-origin from :5174 → :8000 (CORS), proven by probing /readyz + /auth/me
//   3. logs in through the real /v1/auth/login cookie flow (seeded dev user)
//   4. sends one message and the turn streams to completion over the cloud SSE path
//      (web never routes to the local sidecar — capabilities.hasLocalEngine()===false)
//
// Run from apps/server having seeded the dev user once:
//   uv run python scripts/seed_dev_user.py
// then:
//   node apps/desktop/scripts/smoke-webapp.mjs
//
// Env knobs:
//   SMOKE_USER / SMOKE_PASS  login creds (default dev / devpassword — the documented
//                            non-secret seed values, see apps/desktop/.env.example)
//   SMOKE_API                backend base (default http://localhost:8000)
//   SMOKE_PORT               vite port, must be CORS-allowlisted (default 5174)
//   SMOKE_PROMPT             message to send (default a 1-line self-intro, low token)
//   SMOKE_TURN_TIMEOUT_MS    max wait for the turn to finish (default 120000)
//   SMOKE_HEADED=1           run headed (watch it drive the browser)

import { mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";
import { freeListenPorts } from "../../../scripts/free-listen-port.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");

const USER = process.env.SMOKE_USER ?? "dev";
const PASS = process.env.SMOKE_PASS ?? "devpassword";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const PORT = Number(process.env.SMOKE_PORT ?? 5174);
const PROMPT = process.env.SMOKE_PROMPT ?? "你好，请用一句话介绍你自己。";
const TURN_TIMEOUT_MS = Number(process.env.SMOKE_TURN_TIMEOUT_MS ?? 120_000);
const HEADED = process.env.SMOKE_HEADED === "1";
// --boot: CI gate mode. No backend / creds / LLM — just proves the production web
// entry boots in a real browser with the REAL auth gate active (not preview) and
// renders without uncaught errors. The full login+turn smoke (default) needs a
// running backend + seeded dev user and bills one real LLM turn.
const BOOT_ONLY = process.argv.includes("--boot");

const outDir = resolve(desktopDir, "smoke-out");

/** Stamp a step label and (best-effort) a screenshot, so a failure has evidence. */
async function shot(page, name) {
  await page
    .screenshot({ path: resolve(outDir, `${name}.png`) })
    .catch(() => {});
}

async function main() {
  // Run from the desktop package so vite.webapp.config.ts resolves root (src/renderer)
  // exactly as in `pnpm dev:webapp` / `pnpm build:webapp`.
  process.chdir(desktopDir);

  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  const summary = {
    base: null,
    api: API,
    probe: {},
    consoleErrors: [],
    pageErrors: [],
    failedRequests: [],
    authed: false,
    turnStarted: false,
    turnCompleted: false,
    gateRendered: false,
    assistantText: null,
    ok: false,
  };

  // Drop leftover AgentCore vite/smoke listeners before strictPort bind.
  freeListenPorts([PORT]);

  console.log(`Booting web client (vite.webapp.config.ts) on :${PORT}…`);
  /** @type {import("vite").ViteDevServer | undefined} */
  let server;
  /** @type {import("playwright").Browser | undefined} */
  let browser;
  try {
    server = await createServer({
      configFile: resolve(desktopDir, "vite.webapp.config.ts"),
      logLevel: "warn",
      server: { port: PORT, strictPort: true },
    });
    await server.listen();
    const base = server.resolvedUrls?.local?.[0];
    if (!base) {
      throw new Error("Vite did not report a local URL.");
    }
    summary.base = base;
    console.log(`  web client at ${base}`);

    try {
      browser = await chromium.launch({ headless: !HEADED });
    } catch (err) {
      console.error(
        `Failed to launch Chromium. Install it once:\n  pnpm -C apps/desktop exec playwright install chromium\n${String(err?.message ?? err)}`,
      );
      process.exitCode = 1;
      return;
    }

    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 2,
      colorScheme: "light",
    });
    page.on("console", (m) => {
      if (m.type() === "error") summary.consoleErrors.push(m.text());
    });
    page.on("pageerror", (e) => summary.pageErrors.push(e.message));
    page.on("requestfailed", (r) => {
      const u = r.url();
      if (u.includes("/v1/") || u.includes("/readyz") || u.startsWith(API)) {
        summary.failedRequests.push(
          `${r.method()} ${u} — ${r.failure()?.errorText}`,
        );
      }
    });

    try {
      // `/` must be the web entry (same as production). Visiting index.webapp.html
      // still works; the root rewrite is what humans and this smoke both hit.
      const appUrl = base;
      await page.goto(appUrl, { waitUntil: "load", timeout: 30_000 });

      // (2) Backend reachable cross-origin from the browser? A real status (200/401)
      // means CORS + connectivity are fine; a thrown TypeError means CORS/offline.
      summary.probe = await page.evaluate(async (api) => {
        const out = {};
        try {
          const r = await fetch(`${api}/readyz`, { credentials: "include" });
          out.readyz = r.status;
        } catch (e) {
          out.readyzErr = String(e);
        }
        try {
          const r = await fetch(`${api}/v1/auth/me`, { credentials: "include" });
          out.me = r.status;
        } catch (e) {
          out.meErr = String(e);
        }
        out.isWeb = window.__WEB__ === true;
        out.isWebPreview = window.__WEB_PREVIEW__ === true;
        return out;
      }, API);
      console.log("  backend probe:", JSON.stringify(summary.probe));
      await shot(page, "01-loaded");

      // (3) The auth gate resolved to one of its real terminal states — each proves
      // the web entry mounted and the REAL auth flow ran (not stuck on "加载中…",
      // not the offline-preview bypass):
      //   • composer (输入消息)    → authenticated (existing cookie)
      //   • login form (用户名)    → unauthenticated (backend up, no cookie)
      //   • 服务暂时不可用 + 重试   → backend unreachable (the CI / no-backend state)
      const userBox = page.getByPlaceholder("邮箱或用户名");
      const composer = page.getByPlaceholder(/输入消息/);
      const outage = page.getByText("服务暂时不可用");
      await Promise.race([
        userBox.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
        composer.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
        outage.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
      ]);
      const loginVisible = await userBox.isVisible().catch(() => false);
      const composerVisible = await composer.isVisible().catch(() => false);
      const outageVisible = await outage.isVisible().catch(() => false);
      summary.gateRendered = loginVisible || composerVisible || outageVisible;

      // CI gate (--boot): stop after proving the entry booted, the real auth gate is
      // active (__WEB__ && !__WEB_PREVIEW__) and rendered with no uncaught errors. No
      // backend needed — 服务暂时不可用 is the expected no-backend state; the full
      // login+turn smoke below needs a live backend + seeded dev user.
      if (BOOT_ONLY) {
        const state = composerVisible
          ? "authed"
          : loginVisible
            ? "login"
            : outageVisible
              ? "outage"
              : "none";
        console.log(
          `  boot gate: web=${summary.probe.isWeb} preview=${summary.probe.isWebPreview} state=${state} errors=${summary.pageErrors.length}`,
        );
      } else {
        if (loginVisible) {
          console.log(`  login form shown — signing in as ${USER}…`);
          await userBox.fill(USER);
          await page.getByPlaceholder(/密码/).first().fill(PASS);
          await shot(page, "02-login-filled");
          await page.locator('button[type="submit"]').click();
        } else {
          console.log("  already authenticated (existing session).");
        }

        // Authenticated once the composer is mounted.
        await composer.waitFor({ state: "visible", timeout: 30_000 });
        summary.authed = true;
        console.log("  authenticated ✓ (composer mounted)");
        await shot(page, "03-authed");

        // (4) Send one message; the turn streams over the cloud SSE path.
        await composer.click();
        await composer.fill(PROMPT);
        await page.getByRole("button", { name: "发送" }).click();

        // Button flips to 停止生成 while the turn runs, then back — clean start/done signal.
        const stopBtn = page.getByRole("button", { name: "停止生成" });
        try {
          await stopBtn.waitFor({ state: "visible", timeout: 20_000 });
          summary.turnStarted = true;
          console.log("  turn started (streaming) ✓");
          await shot(page, "04-streaming");
        } catch {
          console.log(
            "  WARN: never saw a 停止生成 state (turn may have errored fast).",
          );
        }
        try {
          await stopBtn.waitFor({ state: "hidden", timeout: TURN_TIMEOUT_MS });
          summary.turnCompleted = true;
          console.log("  turn completed ✓");
        } catch {
          console.log("  WARN: turn did not complete within timeout.");
        }

        // Grab the last assistant bubble's text as a sanity snippet.
        summary.assistantText = await page
          .evaluate(() => {
            const nodes = Array.from(
              document.querySelectorAll(
                "[data-message-role='assistant'], .prose, article",
              ),
            );
            const last = nodes[nodes.length - 1];
            const t = (last?.textContent ?? document.body.innerText ?? "").trim();
            return t.slice(-400);
          })
          .catch(() => null);
        await shot(page, "05-done");

        // Gating spot-check: visit surfaces that carry local-only affordances and
        // screenshot them, so the web build's degradation (no window controls, no
        // 「允许本机执行」toggle / 软件更新, no 添加本地文件夹) is visually verifiable. The
        // cookie set during login persists in this context, so a full reload re-auths
        // silently (no login form).
        try {
          const go = async (hash, name) => {
            await page.goto(new URL(`index.webapp.html#${hash}`, base).href, {
              waitUntil: "load",
              timeout: 20_000,
            });
            await page.waitForTimeout(900);
            await shot(page, name);
          };
          await go("/more/model", "06-settings-model");
          await go("/files", "07-files");
        } catch {
          /* best-effort gating screenshots — never fail the smoke on these */
        }
      }

      summary.ok = BOOT_ONLY
        ? !!summary.base &&
          summary.probe.isWeb === true &&
          summary.probe.isWebPreview === false &&
          summary.gateRendered &&
          summary.pageErrors.length === 0
        : summary.authed &&
          summary.turnStarted &&
          summary.turnCompleted &&
          summary.pageErrors.length === 0;
    } catch (err) {
      summary.fatal = String(err?.stack ?? err?.message ?? err);
      await shot(page, "99-fatal");
    }
  } finally {
    // Always tear down Vite + browser — including Chromium launch failure /
    // createServer throw paths that previously could leave :SMOKE_PORT held.
    if (browser) await browser.close().catch(() => {});
    if (server) await server.close().catch(() => {});
  }

  console.log(`\nSMOKE_RESULT ${JSON.stringify(summary)}`);
  console.log(`\nScreenshots → ${outDir}`);
  process.exitCode = summary.ok ? 0 : 1;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
