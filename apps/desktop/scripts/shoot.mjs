// Screenshot harness for the offline AI preview (#/preview).
//
// Boots the renderer as a plain browser app (vite.web.config.ts → main.web.tsx,
// which stubs the four Electron globals), then drives a headless Chromium to each
// committed conformance scenario and writes a PNG per scenario. This is the tight
// loop the AI uses to self-check UI changes: edit a component → `pnpm shoot` →
// read the PNGs in shoot-out/ — no Electron, no backend, no LLM, no tokens.
//
// It also doubles as a CI render smoke gate: a scenario that crashes on render
// (uncaught error, or the page never mounts #/preview) is a failure and the
// process exits non-zero, so a component change can't silently break an AI state.
//
// Usage:
//   node scripts/shoot.mjs                 # terminal state of every scenario
//   node scripts/shoot.mjs debate          # only scenarios whose name includes "debate"
//   SHOOT_FRAMES=3 node scripts/shoot.mjs  # + 3 mid-stream frames per scenario
//   SHOOT_SETTLE_MS=1200 node scripts/shoot.mjs   # longer settle for async graphs
//   SHOOT_WORKERS=4 node scripts/shoot.mjs # parallel Chromium pages (default min(4, CPUs))
//
// Env knobs: SHOOT_FRAMES (default 0 = terminal only; N = N evenly-spaced in-progress
// frames per scenario via #/preview?s=…&k=<count>, file `<name>.f<k>.png`),
// SHOOT_SETTLE_MS (default 800), SHOOT_WIDTH (1440), SHOOT_HEIGHT (900),
// SHOOT_SCALE (2), SHOOT_THEME ("light" | "dark", default light),
// SHOOT_WORKERS (default min(4, CPUs); 1 = old serial page),
// SHOOT_ZOOM ("" default | e.g. "compare" [旧别名 "revisions"] → appends &zoom=<v>
// to deep-link the turn-detail 放大态 view [对比 / 群聊 / …] otherwise only reachable by clicking;
// pair with a longer SHOOT_SETTLE_MS, e.g. 1800, and a scenario filter like `revision`),
// SHOOT_CLICK ("" default | button accessible-name → after settle, click the first
// matching button then re-settle before the shot, to capture an interaction-gated state
// like「对比两版」/「对比发言」; a scenario without the button is left as-is, not failed).

import { mkdir, readFile, readdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { isTurnFixture } from "@agentcore/protocol-conformance/fixtureKind";
import { chromium } from "playwright";
import { createServer } from "vite";
import {
  buildShots,
  resolveWorkerCount,
  shardScenarios,
} from "./shoot-lib.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const repoRoot = resolve(desktopDir, "..", "..");
const fixturesDir = resolve(repoRoot, "packages/protocol-conformance/fixtures");
// Dev-only screenshot output. MUST stay outside electron-vite `out/` — electron-builder
// packs `out/**` into the installer (see electron-builder.config.mjs `files`).
const SHOOT_OUT_DIR = "shoot-out";
const outDir = resolve(desktopDir, SHOOT_OUT_DIR);

const SETTLE_MS = Number(process.env.SHOOT_SETTLE_MS ?? 800);
const VIEWPORT = {
  width: Number(process.env.SHOOT_WIDTH ?? 1440),
  height: Number(process.env.SHOOT_HEIGHT ?? 900),
};
const SCALE = Number(process.env.SHOOT_SCALE ?? 2);
const THEME = process.env.SHOOT_THEME === "dark" ? "dark" : "light";
const FRAMES = Math.max(0, Number(process.env.SHOOT_FRAMES ?? 0) | 0);
const ZOOM = process.env.SHOOT_ZOOM ?? "";
const CLICK = process.env.SHOOT_CLICK ?? "";
const filter = (process.argv[2] ?? "").toLowerCase();

async function loadScenarios() {
  const files = (await readdir(fixturesDir))
    .filter((f) => f.endsWith(".json"))
    .sort();
  const scenarios = [];
  for (const file of files) {
    const raw = JSON.parse(await readFile(resolve(fixturesDir, file), "utf8"));
    if (!isTurnFixture(raw)) continue;
    scenarios.push({
      name: raw.name,
      description: raw.description ?? "",
      events: raw.events.length,
    });
  }
  return scenarios;
}

async function preparePage(browser) {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: SCALE,
    colorScheme: THEME,
  });
  // The app's theme is class-based via its store (lib/theme.ts → `.dark` on <html>),
  // seeded from localStorage `agentcore:theme`; `colorScheme` alone only drives the
  // `system` choice and did not flip the preview here. Seed the store key so
  // SHOOT_THEME deterministically selects light/dark on every navigation.
  // Reset side-panel visibility on every document boot so a scenario that
  // auto-opens the right dock cannot leak into a later shot on this page
  // (full reload per scenario, via `?shoot=<generation>`). Preview UI storage
  // is in-memory, so the document boot is what actually isolates zustand.
  await context.addInitScript(
    (theme) => {
      try {
        localStorage.setItem("agentcore:theme", theme);
        localStorage.setItem("agentcore:side-panel-open", "false");
      } catch {
        /* localStorage unavailable — fall back to colorScheme */
      }
    },
    THEME,
  );
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));
  return { context, page, pageErrors };
}

async function captureShot(page, pageErrors, shot, { base, outDir: dest, label }) {
  pageErrors.length = 0;
  let failure = null;
  const shotStarted = performance.now();
  const { scenarioChanged, bootGeneration } = label;
  try {
    const url = new URL("index.web.html", base);
    url.searchParams.set("shoot", String(bootGeneration));
    const zoomSuffix = ZOOM ? `&zoom=${encodeURIComponent(ZOOM)}` : "";
    url.hash =
      shot.k === null
        ? `/preview?s=${encodeURIComponent(shot.name)}${zoomSuffix}`
        : `/preview?s=${encodeURIComponent(shot.name)}&k=${shot.k}${zoomSuffix}`;
    try {
      await page.goto(url.href, {
        waitUntil: scenarioChanged ? "load" : "domcontentloaded",
        timeout: 30_000,
      });
    } catch (err) {
      // Fast frame scrubs can interrupt an in-flight navigation; one retry is enough.
      if (!String(err?.message ?? err).includes("interrupted")) throw err;
      await page.goto(url.href, {
        waitUntil: scenarioChanged ? "load" : "domcontentloaded",
        timeout: 30_000,
      });
    }
    const frameSel = shot.k === null ? "full" : String(shot.k);
    // Seed always lands on #/preview first (`data-preview-scenario`). With
    // SHOOT_ZOOM, PreviewPage then navigates to turn-detail — wait for that
    // URL so we don't screenshot a no-op preview (假绿).
    await page.waitForSelector(
      `[data-preview-scenario="${shot.name}"][data-preview-frame="${frameSel}"]`,
      { timeout: 15_000 },
    );
    if (ZOOM) {
      await page.waitForURL(/#\/conversations\/preview-.*\/turn\//, {
        timeout: 10_000,
      });
    }
    // Let any in-flight client-side navigation from the prior frame scrub settle.
    await page.waitForLoadState("domcontentloaded").catch(() => {});
    await page.evaluate(() => document.fonts?.ready).catch(() => {});
    // Let async renderers (elk team-graph layout, mermaid, katex) settle.
    await page.waitForTimeout(SETTLE_MS);
    // Optional interaction-gated state: click a button by accessible name, then
    // re-settle. Absent target is fine (not every scenario has it).
    if (CLICK) {
      await page
        .getByRole("button", { name: CLICK })
        .first()
        .click({ timeout: 3000 })
        .then(() => page.waitForTimeout(500))
        .catch(() => {});
    }
  } catch (err) {
    failure = String(err?.message ?? err);
  }
  // Always shoot — even on failure — so a red CI gate has visual evidence (e.g.
  // the RouteError fallback) to upload as an artifact.
  await page.screenshot({ path: resolve(dest, shot.file) }).catch(() => {});
  if (pageErrors.length) {
    failure = `${failure ? `${failure}; ` : ""}page error: ${pageErrors.join(" | ")}`;
  }
  return { failure, ms: Math.round(performance.now() - shotStarted) };
}

async function shootOnPage(page, pageErrors, shots, { base, dest, onProgress }) {
  let ok = 0;
  const failures = [];
  /** Full Vite/React boot only when the scenario changes; frame scrubs reuse the page. */
  let bootGeneration = 0;
  let lastScenarioName = null;
  for (const shot of shots) {
    const scenarioChanged = shot.name !== lastScenarioName;
    if (scenarioChanged) {
      bootGeneration += 1;
      lastScenarioName = shot.name;
    }
    const { failure, ms } = await captureShot(page, pageErrors, shot, {
      base,
      outDir: dest,
      label: { scenarioChanged, bootGeneration },
    });
    if (failure) {
      failures.push({ name: shot.file, error: failure });
      onProgress({ file: shot.file, ms, failure });
    } else {
      ok += 1;
      onProgress({ file: shot.file, ms, failure: null });
    }
  }
  return { ok, failures };
}

async function main() {
  // Run from the desktop package so vite.web.config.ts resolves its relative root
  // (src/renderer) and workspace-root fs allowlist exactly as designed.
  process.chdir(desktopDir);

  let scenarios = await loadScenarios();
  if (filter) {
    scenarios = scenarios.filter((s) => s.name.toLowerCase().includes(filter));
  }
  if (scenarios.length === 0) {
    console.error(
      filter
        ? `No scenarios matched filter "${filter}".`
        : `No fixtures found in ${fixturesDir}.`,
    );
    process.exitCode = 1;
    return;
  }

  // Fresh output dir so shots from deleted/renamed scenarios never linger.
  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  const allShots = buildShots(scenarios, FRAMES);
  const workersN = resolveWorkerCount({ scenarioCount: scenarios.length });
  console.log(
    `Shooting ${allShots.length} shots / ${scenarios.length} scenarios with ${workersN} worker(s) (SHOOT_FRAMES=${FRAMES})`,
  );

  console.log("Booting web preview (vite.web.config.ts)…");
  const server = await createServer({
    configFile: resolve(desktopDir, "vite.web.config.ts"),
    logLevel: "warn",
    server: {
      warmup: {
        clientFiles: [
          "./index.web.html",
          "./main.web.tsx",
          "./pages/PreviewPage.tsx",
        ],
      },
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
      `Failed to launch Chromium. Install the Playwright browser once:\n  pnpm -C apps/desktop exec playwright install chromium\n${String(err?.message ?? err)}`,
    );
    process.exitCode = 1;
    return;
  }

  const t0 = performance.now();
  let ok = 0;
  const failures = [];
  try {
    // One real preview boot so Vite's transform cache is hot before workers race.
    console.log("Warming Vite (first #/preview boot)…");
    const warm = await preparePage(browser);
    try {
      const warmUrl = new URL("index.web.html", base);
      warmUrl.hash = "/preview";
      await warm.page.goto(warmUrl.href, { waitUntil: "load", timeout: 60_000 });
      await warm.page.waitForSelector("[data-preview-scenario]", {
        timeout: 60_000,
      });
    } finally {
      await warm.context.close();
    }

    let completed = 0;
    const onProgress = ({ file, ms, failure }) => {
      completed += 1;
      const tag = `[${completed}/${allShots.length}] ${file}`;
      if (failure) console.error(`  \u2717 ${tag} — ${failure}`);
      else console.log(`  \u2713 ${tag} (${ms}ms)`);
    };

    const shards = shardScenarios(scenarios, workersN);
    const results = await Promise.all(
      shards.map(async (shard) => {
        const { context, page, pageErrors } = await preparePage(browser);
        try {
          return await shootOnPage(page, pageErrors, buildShots(shard, FRAMES), {
            base,
            dest: outDir,
            onProgress,
          });
        } finally {
          await context.close();
        }
      }),
    );
    for (const r of results) {
      ok += r.ok;
      failures.push(...r.failures);
    }
  } finally {
    await browser.close();
    await server.close();
  }

  const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
  console.log(
    `\nDone: ${ok}/${allShots.length} in ${elapsed}s → ${outDir} (${workersN} workers)`,
  );
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
