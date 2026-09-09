// Screenshot harness for the 设置区 (#/more/* sub-pages) — one PNG per settings
// sub-page so AI can read back what a UI change actually looks like.
//
// Usage:
//   node scripts/shoot-settings.mjs
//   node scripts/shoot-settings.mjs account          # substring filter on the page id
//   pnpm -C apps/desktop shoot:settings
//   SHOOT_THEME=dark pnpm -C apps/desktop shoot:settings
//
// Mechanism — the union of two harnesses that already exist here, nothing new:
//   • webapp 壳 (vite.webapp.config.ts → index.webapp.html, main.webapp.tsx) with the
//     REAL AuthGate, exactly like e2e (`e2e/playwright.config.ts`) and smoke-webapp.mjs.
//     The offline preview entry (index.web.html) sets `__WEB_PREVIEW__`, which makes
//     AuthGate skip bootstrap and leaves the auth store empty — 账户设置 would then
//     render a blank profile. Settings需要登录态, so we boot the real-auth entry and
//     satisfy it with a stubbed `/v1/auth/me`.
//   • Playwright `page.route` REST stubs (soft-stub posture, per-endpoint fixtures
//     so the pages render data instead of empty/error states). No product code is
//     touched to make this work.
//
// `VITE_API_URL` is pinned to "" so every request is SAME-ORIGIN against the Vite dev
// server (the `define` trick from e2e/vite.e2e.config.ts). That keeps CORS/preflight out
// of the picture entirely — a cross-origin `route.fulfill` would need hand-rolled CORS
// headers or the browser drops the response.
//
// Known gaps vs the real Electron app (screenshots differ, product is fine):
//   • Overlay scrollbars: headless Chromium's scrollbars take no width, so bugs where a
//     scrollbar gutter clips content cannot show up here (frontend-preview.mdc).
//     SHOOT_FIT (default on) grows the viewport to the full page height, which removes
//     the scroll container's overflow altogether — set SHOOT_FIT=0 for a fixed 1440x900
//     shot that at least keeps the page scrollable.
//   • Browser runtime (`__WEB__`) means capability-gated desktop-only blocks are absent:
//     关于 loses 「软件更新」/「更新通道」/「允许本机执行」 (hasAutoUpdater / hasLocalEngine),
//     and the web client hides the desktop TitleBar (isWebClient). Everything inside the
//     settings pane itself renders identically.
//
// Env knobs: SHOOT_THEME=dark · SHOOT_WIDTH · SHOOT_HEIGHT · SHOOT_SCALE ·
//            SHOOT_SETTLE_MS · SHOOT_FIT=0 · SHOOT_MAX_HEIGHT

import { mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const SHOOT_OUT_DIR = "shoot-out-settings";
const outDir = resolve(desktopDir, SHOOT_OUT_DIR);

const SETTLE_MS = Number(process.env.SHOOT_SETTLE_MS ?? 900);
const VIEWPORT = {
  width: Number(process.env.SHOOT_WIDTH ?? 1440),
  height: Number(process.env.SHOOT_HEIGHT ?? 900),
};
const SCALE = Number(process.env.SHOOT_SCALE ?? 2);
const THEME = process.env.SHOOT_THEME === "dark" ? "dark" : "light";
// Grow the viewport to the sub-page's full height so one PNG shows the whole page.
const FIT = process.env.SHOOT_FIT !== "0";
const MAX_HEIGHT = Number(process.env.SHOOT_MAX_HEIGHT ?? 4000);
const filter = (process.argv[2] ?? "").toLowerCase();

/**
 * The 9 settings sub-pages, in 设置 nav order (MorePage NAV_GROUPS). `heading` is the
 * `<h1>` SettingsHeader renders — the render marker, so no `data-*` hook is needed in
 * product code. `ready` is an optional second marker that only exists once the page's
 * data arrived, for pages whose whole body is behind a query (see waitForLoaded).
 * `skip` marks a page we cannot stand up offline (none today); the reason is printed
 * instead of a screenshot.
 */
const PAGES = [
  { id: "01-model", hash: "/more/model", heading: "模型" },
  { id: "02-providers", hash: "/more/providers", heading: "服务商" },
  { id: "03-account", hash: "/more/account", heading: "账户设置" },
  { id: "04-git", hash: "/more/git", heading: "Git 凭据" },
  { id: "05-usage", hash: "/more/usage", heading: "用量", ready: "本月额度" },
  { id: "06-messages", hash: "/more/messages", heading: "消息隐私" },
  { id: "07-general", hash: "/more/general", heading: "通用" },
  { id: "08-shortcuts", hash: "/more/shortcuts", heading: "快捷键" },
  { id: "09-about", hash: "/more/about", heading: "关于 AgentCore" },
];

// ---------------------------------------------------------------------------
// REST fixtures — shapes follow the OpenAPI DTOs in
// packages/contract-rest-types (UserResponse / LlmProvidersResponse /
// ModelCatalogResponse / LlmModelProfileListResponse / UsageSummary /
// GitCredentialView / SessionListResponse / DirectorySettings). Values are
// synthetic demo data, deliberately non-empty so each page shows its populated
// state rather than an empty shell.
// ---------------------------------------------------------------------------

const ISO = "2026-08-01T09:00:00.000Z";
const minutesAgo = (m) => new Date(Date.now() - m * 60_000).toISOString();

const MOCK_USER = {
  id: "user_shoot",
  username: "dev",
  display_name: "自检账号",
  email: "dev@example.com",
  role: "user",
  created_at: ISO,
  password_must_change: false,
  avatar_url: null,
};

const PROVIDERS = {
  billing_mode: "platform",
  default_model_profile_id: "profile_default",
  platform_available: true,
  platform_model: "deepseek-v4-flash",
  providers: [
    {
      id: "prov_deepseek",
      label: "DeepSeek",
      base_url: "https://api.deepseek.com",
      default_model: "deepseek-v4-flash",
      masked_key: "sk-****3f9a",
      message: "连通正常",
      status: "active",
      supports_tools: true,
      created_at: ISO,
      updated_at: ISO,
    },
    {
      id: "prov_zen",
      label: "OpenCode Zen",
      base_url: "https://opencode.ai/zen/v1",
      default_model: "kimi-k2.6",
      masked_key: "sk-****7b21",
      message: "上次测试失败：401 未授权",
      status: "error",
      supports_tools: null,
      created_at: ISO,
      updated_at: ISO,
    },
  ],
};

const MODEL_CATALOG = {
  byok_configured: true,
  current: { id: "deepseek-v4-flash", origin: "platform", provider_id: null },
  models: [
    {
      id: "deepseek-v4-flash",
      display_name: "DeepSeek V4 Flash",
      origin: "platform",
      vendor: "deepseek",
      available: true,
      badge: "免费额度",
      capabilities: ["tools", "reasoning"],
      context_length: 131072,
      price: null,
      provider_id: null,
      provider_label: null,
    },
    {
      id: "deepseek-v4-pro",
      display_name: "DeepSeek V4 Pro",
      origin: "byok",
      vendor: "deepseek",
      available: true,
      badge: null,
      capabilities: ["tools", "vision", "reasoning"],
      context_length: 131072,
      price: null,
      provider_id: "prov_deepseek",
      provider_label: "DeepSeek",
    },
    {
      id: "kimi-k2.6",
      display_name: "Kimi K2.6",
      origin: "byok",
      vendor: "moonshot",
      available: false,
      badge: null,
      capabilities: ["tools"],
      context_length: 262144,
      price: null,
      provider_id: "prov_zen",
      provider_label: "OpenCode Zen",
    },
  ],
};

const platformSlot = (model) => ({ model, origin: "platform", provider_id: null });
const byokSlot = (model, providerId) => ({
  model,
  origin: "byok",
  provider_id: providerId,
});

const MODEL_PROFILES = {
  default_model_profile_id: "profile_default",
  data: [
    {
      id: "profile_default",
      name: "默认组合",
      kind: "system",
      is_default: true,
      main: platformSlot("deepseek-v4-flash"),
      worker: null,
      background: null,
      vision: null,
      warnings: [],
      created_at: ISO,
      updated_at: ISO,
    },
    {
      id: "profile_research",
      name: "深度研究",
      kind: "user",
      is_default: false,
      main: byokSlot("deepseek-v4-pro", "prov_deepseek"),
      worker: platformSlot("deepseek-v4-flash"),
      background: platformSlot("deepseek-v4-flash"),
      vision: byokSlot("deepseek-v4-pro", "prov_deepseek"),
      warnings: [],
      created_at: ISO,
      updated_at: ISO,
    },
  ],
};

/** Money is integer nano-CNY end-to-end (UI renders ¥ = nano / 1e9). */
const usageWindow = ({ input, output, cacheHit, requests, costNano }) => ({
  cost: {
    cached: Math.round(costNano * 0.12),
    cny_total: costNano,
    currency: "CNY",
    input: Math.round(costNano * 0.4),
    output: Math.round(costNano * 0.6),
    pricing_source: "curated",
    total: costNano,
  },
  estimated_cost: null,
  requests,
  usage: {
    cache_hit: cacheHit,
    cache_miss: Math.max(input - cacheHit, 0),
    error: null,
    input,
    output,
    reasoning: Math.round(output * 0.3),
  },
});

const USAGE_SUMMARY = {
  billing_mode: "platform",
  today: usageWindow({
    input: 486_200,
    output: 92_400,
    cacheHit: 301_500,
    requests: 137,
    costNano: 2_360_000_000,
  }),
  month: usageWindow({
    input: 7_412_000,
    output: 1_385_000,
    cacheHit: 4_902_000,
    requests: 1962,
    costNano: 31_480_000_000,
  }),
  quota: {
    daily_cost_nano: 5_000_000_000,
    daily_requests: 500,
    daily_tokens: 2_000_000,
    monthly_cost_nano: 50_000_000_000,
  },
  recent_daily_cost: [
    { date: "2026-07-26", cost_total: 1_820_000_000 },
    { date: "2026-07-27", cost_total: 640_000_000 },
    { date: "2026-07-28", cost_total: 4_150_000_000 },
    { date: "2026-07-29", cost_total: 3_070_000_000 },
    { date: "2026-07-30", cost_total: 5_930_000_000 },
    { date: "2026-07-31", cost_total: 2_410_000_000 },
    { date: "2026-08-01", cost_total: 2_360_000_000 },
  ],
};

const SESSIONS = {
  total: 2,
  data: [
    {
      id: "sess_current",
      current: true,
      created_at: minutesAgo(60 * 26),
      last_used_at: minutesAgo(2),
      ip: "203.0.113.24",
      platform: "windows",
      user_agent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AgentCore/0.9.0 Chrome/140 Electron/42",
    },
    {
      id: "sess_phone",
      current: false,
      created_at: minutesAgo(60 * 24 * 9),
      last_used_at: minutesAgo(60 * 31),
      ip: "198.51.100.7",
      platform: "android",
      user_agent: "Mozilla/5.0 (Linux; Android 15; Pixel 9) AgentCore/0.9.0",
    },
  ],
};

/** Exact-path fixtures (query string stripped). */
const FIXTURES = new Map([
  ["/readyz", { status: "ready", database: true }],
  [
    "/version",
    { version: "0.9.0", git_sha: "1a2b3c4d5e6f7a8b", built_at: ISO },
  ],
  ["/updates/policy", { enabled: true, min_desktop_version: null }],

  ["/v1/auth/me", MOCK_USER],
  ["/v1/auth/sessions", SESSIONS],

  ["/v1/users/me/llm-providers", PROVIDERS],
  ["/v1/users/me/models", MODEL_CATALOG],
  ["/v1/users/me/llm-model-profiles", MODEL_PROFILES],
  [
    "/v1/users/me/git-credentials",
    {
      configured: true,
      masked_token: "ghp_****9f2c",
      username: "x-access-token",
      updated_at: ISO,
    },
  ],
  ["/v1/users/me/autonomy", { policy: "less_interrupt" }],

  ["/v1/usage/summary", USAGE_SUMMARY],
  [
    "/v1/messages/directory",
    { discoverable: true, who_can_dm: "anyone", who_can_friend: "group_members" },
  ],
  ["/v1/messages/chats", { data: [], total: 0 }],
  ["/v1/messages/friends", { data: [], total: 0 }],
  ["/v1/messages/blocks", { data: [], total: 0 }],

  // Ambient shell chrome (sidebar / banners / badges) — quiet, empty states.
  ["/v1/notices/active", { banner: null, modal: null, inbox: [] }],
  ["/v1/conversations", { data: [], page: 1, page_size: 100, total: 0 }],
  ["/v1/conversations/grouped", { folders: [], ungrouped: [] }],
  ["/v1/folders", []],
  ["/v1/workspaces", { data: [], total: 0 }],
  ["/v1/capabilities", {}],
]);

/** Endpoints that speak SSE — answer with an immediately-closed stream so the
 *  shell's firehoses back off instead of hammering a JSON 200. */
const SSE_PATHS = new Set(["/v1/realtime", "/v1/fulfill"]);

async function fulfillApi(route) {
  const { pathname } = new URL(route.request().url());

  if (SSE_PATHS.has(pathname)) {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: ": shoot-settings stub\n\n",
    });
    return;
  }

  const fixture = FIXTURES.get(pathname);
  if (fixture !== undefined) {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(fixture),
    });
    return;
  }

  // Unknown read: the list shape covers most collection routes and keeps
  // consumers on their empty state rather than an error banner.
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], items: [], total: 0 }),
  });
}

/** How long a sub-page gets to finish its queries before we shoot it anyway. */
const LOAD_TIMEOUT_MS = 15_000;

/**
 * Wait until the sub-page stopped loading, so we never shoot a spinner.
 *
 * The obvious `getByText("加载中…").waitFor({ state: "detached" })` rules nothing
 * out: a locator matching no element already counts as detached, so that wait
 * returns instantly both before the spinner mounts (a page's `<h1>` can commit a
 * tick ahead of the query that fills its body — how 用量 kept getting shot
 * mid-spinner) and between two spinners on a page that loads in stages. Poll for a
 * stable absence instead, after the page's own `ready` marker when it has one.
 */
async function waitForLoaded(page, spec) {
  if (spec.ready) {
    await page
      .getByText(spec.ready)
      .first()
      .waitFor({ state: "visible", timeout: LOAD_TIMEOUT_MS })
      .catch(() => {});
  }

  const spinner = page.getByText("加载中…");
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  let quiet = 0;
  while (quiet < 2) {
    const count = await spinner.count().catch(() => 0);
    quiet = count === 0 ? quiet + 1 : 0;
    if (quiet >= 2 || Date.now() >= deadline) break;
    await page.waitForTimeout(200);
  }
  return quiet >= 2;
}

/**
 * Overflow (in px) of the scroll container that owns the settings pane, found by
 * walking up from the page `<h1>`; falls back to the document scroller. 0 when
 * everything already fits.
 */
async function measureOverflow(page) {
  return page.evaluate(() => {
    const heading = document.querySelector("h1");
    let el = heading?.parentElement ?? null;
    while (el && el !== document.body) {
      const overflowY = getComputedStyle(el).overflowY;
      if (
        (overflowY === "auto" || overflowY === "scroll") &&
        el.scrollHeight > el.clientHeight + 1
      ) {
        return el.scrollHeight - el.clientHeight;
      }
      el = el.parentElement;
    }
    const doc = document.scrollingElement ?? document.documentElement;
    return Math.max(doc.scrollHeight - doc.clientHeight, 0);
  });
}

/**
 * Grow the viewport until the settings pane stops overflowing, so one PNG holds
 * the whole sub-page.
 *
 * Two things make this iterative rather than a single measure-then-resize:
 * growing the viewport reflows content (wider rows wrap shorter), and a query
 * that resolves late (登录设备 on 账户设置) can turn a page that measured as
 * fitting into one that overflows. So a zero reading only ends the loop when
 * the next one confirms it.
 */
async function fitViewport(page) {
  let settled = 0;
  for (let pass = 0; pass < 6 && settled < 2; pass += 1) {
    const overflow = await measureOverflow(page);
    if (overflow <= 0) {
      settled += 1;
      await page.waitForTimeout(250);
      continue;
    }
    settled = 0;
    const current = page.viewportSize()?.height ?? VIEWPORT.height;
    const height = Math.min(current + overflow + 24, MAX_HEIGHT);
    if (height <= current) break;
    await page.setViewportSize({ width: VIEWPORT.width, height });
    await page.waitForTimeout(250);
  }
}

async function main() {
  process.chdir(desktopDir);

  let pages = PAGES;
  if (filter) {
    pages = pages.filter(
      (p) =>
        p.id.toLowerCase().includes(filter) ||
        p.hash.toLowerCase().includes(filter),
    );
  }
  if (pages.length === 0) {
    console.error(`No settings pages matched filter "${filter}".`);
    process.exitCode = 1;
    return;
  }

  // A filtered run refreshes just the pages it shot; only a full run starts clean.
  if (!filter) await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  console.log("Booting webapp shell (vite.webapp.config.ts, same-origin API)…");
  const server = await createServer({
    configFile: resolve(desktopDir, "vite.webapp.config.ts"),
    logLevel: "warn",
    // Same-origin API (no CORS on stubbed responses) + no dev auto-login racing
    // the stubbed /v1/auth/me. Mirrors e2e/vite.e2e.config.ts's `define` pin,
    // which exists because .env load order is brittle on Windows.
    define: {
      "import.meta.env.VITE_API_URL": '""',
      "import.meta.env.VITE_DEV_USERNAME": '""',
      "import.meta.env.VITE_DEV_PASSWORD": '""',
    },
  });
  await server.listen();
  const base = server.resolvedUrls?.local?.[0];
  if (!base) {
    await server.close();
    throw new Error("Vite did not report a local URL.");
  }

  let browser;
  try {
    browser = await chromium.launch();
  } catch (err) {
    await server.close();
    console.error(
      `Failed to launch Chromium. Install once:\n  pnpm -C apps/desktop exec playwright install chromium\n${String(err?.message ?? err)}`,
    );
    process.exitCode = 1;
    return;
  }

  const page = await browser.newPage({
    viewport: VIEWPORT,
    deviceScaleFactor: SCALE,
    colorScheme: THEME,
  });
  await page.addInitScript((theme) => {
    try {
      // uiStorage namespace + JSON value (stores/ui.ts loadTheme).
      localStorage.setItem("agentcore:theme", JSON.stringify(theme));
    } catch {
      /* ignore */
    }
  }, THEME);

  await page.route("**/v1/**", fulfillApi);
  await page.route("**/readyz", fulfillApi);
  await page.route("**/version", fulfillApi);
  await page.route("**/updates/policy", fulfillApi);

  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));

  // Warm-up pass (not captured): the first navigation of a cold Vite dev server
  // spends seconds transforming the module graph, which is long enough that the
  // first sub-page gets shot while its queries are still 加载中.
  try {
    const warm = new URL("index.webapp.html", base);
    warm.hash = PAGES[0].hash;
    await page.goto(warm.href, { waitUntil: "load", timeout: 60_000 });
    await page.locator("h1").first().waitFor({ timeout: 30_000 });
    await page.waitForTimeout(SETTLE_MS);
  } catch {
    /* best-effort warm-up — the per-page loop reports real failures */
  }

  let ok = 0;
  const failures = [];
  const skipped = [];

  for (const [i, spec] of pages.entries()) {
    const file = `${spec.id}${THEME === "dark" ? "-dark" : ""}.png`;
    const label = `[${i + 1}/${pages.length}] ${file}`;

    if (spec.skip) {
      skipped.push({ name: file, reason: spec.skip });
      console.log(`  – ${label} — skipped: ${spec.skip}`);
      continue;
    }

    pageErrors.length = 0;
    let failure = null;
    let note = "";
    await page.setViewportSize(VIEWPORT).catch(() => {});
    try {
      const url = new URL("index.webapp.html", base);
      url.searchParams.set("shoot-settings", spec.id);
      url.hash = spec.hash;
      await page.goto(url.href, { waitUntil: "load", timeout: 30_000 });
      // AuthGate resolves (stubbed /v1/auth/me) → MorePage → the sub-page's
      // SettingsHeader <h1>. Waiting on the heading covers the whole chain.
      await page
        .locator("h1", { hasText: spec.heading })
        .first()
        .waitFor({ state: "visible", timeout: 20_000 });
      if (!(await waitForLoaded(page, spec))) {
        note = " — 仍有「加载中…」，图里可能是加载态";
      }
      await page.evaluate(() => document.fonts?.ready).catch(() => {});
      await page.waitForTimeout(SETTLE_MS);

      if (FIT) await fitViewport(page);
    } catch (err) {
      failure = String(err?.message ?? err);
    }

    await page.screenshot({ path: resolve(outDir, file) }).catch(() => {});
    if (pageErrors.length) {
      failure = `${failure ? `${failure}; ` : ""}page error: ${pageErrors.join(" | ")}`;
    }
    if (failure) {
      failures.push({ name: file, error: failure });
      console.error(`  ✗ ${label} — ${failure}`);
    } else {
      ok += 1;
      const h = page.viewportSize()?.height ?? VIEWPORT.height;
      console.log(`  ✓ ${label} (${VIEWPORT.width}x${h})${note}`);
    }
  }

  await browser.close();
  await server.close();

  console.log(`\nDone: ${ok}/${pages.length} → ${outDir}`);
  if (skipped.length) {
    console.log(`${skipped.length} skipped:`);
    for (const s of skipped) console.log(`  - ${s.name}: ${s.reason}`);
  }
  if (failures.length) {
    console.error(`${failures.length} failed:`);
    for (const f of failures) console.error(`  - ${f.name}: ${f.error}`);
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
