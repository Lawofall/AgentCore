// Collaboration-graph jank probe: boot offline #/preview, enable __graphPerf,
// flood dense run_output_delta via __graphStress while GraphView is mounted,
// sample rAF frame gaps + longtasks, write JSON under shoot-out-graph-perf/.
//
// Usage:
//   node scripts/shoot-graph-perf.mjs
//   node scripts/shoot-graph-perf.mjs multi_agent_debate_multibeat
//   pnpm -C apps/desktop exec node scripts/shoot-graph-perf.mjs multi_agent_two_act_lv
//
// Args: [scenario] [midframeK]
//   scenario  — default multi_agent_debate_multibeat
//   midframeK — optional prefix frame count (default 22); use "full" for terminal

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const outDir = resolve(desktopDir, "shoot-out-graph-perf");
const SCENARIO = process.argv[2] || "multi_agent_debate_multibeat";
const FRAME_ARG = process.argv[3] || "22";
const STRESS_MS = 3000;
const BASELINE_MS = 1500;

function pct(sorted, p) {
  if (!sorted.length) return 0;
  const i = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil((p / 100) * sorted.length) - 1),
  );
  return sorted[i] ?? 0;
}

function summarizeGaps(gaps) {
  const sorted = [...gaps].sort((a, b) => a - b);
  return {
    n: gaps.length,
    p50: Math.round(pct(sorted, 50) * 10) / 10,
    p95: Math.round(pct(sorted, 95) * 10) / 10,
    max: Math.round((sorted[sorted.length - 1] ?? 0) * 10) / 10,
    over20ms: gaps.filter((v) => v > 20).length,
    over33ms: gaps.filter((v) => v > 33).length,
    over50ms: gaps.filter((v) => v > 50).length,
  };
}

async function waitPreview(page, scenario, frame) {
  const frameAttr = frame === "full" ? "full" : String(frame);
  await page.waitForSelector(
    `[data-preview-scenario="${scenario}"][data-preview-frame="${frameAttr}"]`,
    { timeout: 30_000 },
  );
  await page.evaluate(() => document.fonts?.ready).catch(() => {});
  await page.waitForTimeout(2000);
}

async function sampleRaf(page, durationMs) {
  return page.evaluate(async (ms) => {
    const gaps = [];
    let last = performance.now();
    const start = last;
    await new Promise((resolve) => {
      const step = (now) => {
        gaps.push(now - last);
        last = now;
        if (now - start >= ms) resolve();
        else requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
    return gaps;
  }, durationMs);
}

async function main() {
  process.chdir(desktopDir);
  await mkdir(outDir, { recursive: true });

  console.log("Booting vite…");
  const server = await createServer({
    configFile: resolve(desktopDir, "vite.web.config.ts"),
    logLevel: "warn",
  });
  await server.listen();
  const base = server.resolvedUrls?.local?.[0];
  if (!base) {
    await server.close();
    throw new Error("no vite URL");
  }

  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "light",
  });
  await page.addInitScript(() => {
    try {
      localStorage.setItem("agentcore:theme", "light");
      localStorage.setItem("agentcore:side-panel-open", "false");
    } catch {
      /* */
    }
  });

  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.message ?? err)));

  const frameHash =
    FRAME_ARG === "full"
      ? `/preview?s=${SCENARIO}`
      : `/preview?s=${SCENARIO}&k=${FRAME_ARG}`;
  const url = new URL("index.web.html", base);
  url.hash = frameHash;
  console.log("GOTO", url.href);
  await page.goto(url.href, { waitUntil: "load", timeout: 60_000 });
  await waitPreview(
    page,
    SCENARIO,
    FRAME_ARG === "full" ? "full" : Number(FRAME_ARG),
  );

  const boot = await page.evaluate(() => {
    const rf = document.querySelectorAll(".react-flow").length;
    const nodes = document.querySelectorAll(".react-flow__node").length;
    const hasPerf = typeof window.__graphPerf === "function";
    const hasStress = typeof window.__graphStress === "function";
    if (hasPerf) window.__graphPerf(true);
    if (hasPerf && window.__graphPerf.clear) window.__graphPerf.clear();
    return { rf, nodes, hasPerf, hasStress };
  });
  console.log("boot", boot);
  if (!boot.hasPerf || !boot.hasStress) {
    await browser.close();
    await server.close();
    throw new Error(
      `missing probes perf=${boot.hasPerf} stress=${boot.hasStress}`,
    );
  }
  if (boot.rf < 1) {
    await browser.close();
    await server.close();
    throw new Error("no react-flow mounted — pick a midframe with a graph");
  }

  console.log("baseline rAF…");
  const baselineGaps = await sampleRaf(page, BASELINE_MS);

  console.log("stress flood…");
  const stress = await page.evaluate(async (ms) => {
    const longTasks = [];
    let obs = null;
    try {
      obs = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          longTasks.push({
            ms: Math.round(e.duration * 10) / 10,
            start: Math.round(e.startTime),
          });
        }
      });
      obs.observe({ type: "longtask", buffered: false });
    } catch {
      obs = null;
    }
    const result = await window.__graphStress({ durationMs: ms });
    obs?.disconnect();
    const summary =
      typeof window.__graphPerf?.summary === "function"
        ? window.__graphPerf.summary()
        : null;
    const dump =
      typeof window.__graphPerf?.dump === "function"
        ? window.__graphPerf.dump()
        : [];
    return { result, longTasks, summary, dumpCount: dump.length };
  }, STRESS_MS);

  console.log("stress rAF (tail sample while settling)…");
  // Sample during last portion: re-run a short flood+sample overlap
  const during = await page.evaluate(async (ms) => {
    const gaps = [];
    let last = performance.now();
    const start = last;
    const stressP = window.__graphStress({ durationMs: ms });
    await new Promise((resolve) => {
      const step = (now) => {
        gaps.push(now - last);
        last = now;
        if (now - start >= ms) resolve();
        else requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
    const result = await stressP;
    const summary =
      typeof window.__graphPerf?.summary === "function"
        ? window.__graphPerf.summary()
        : null;
    return { gaps, result, summary };
  }, STRESS_MS);

  const shotPath = resolve(outDir, `${SCENARIO}-stress.png`);
  await page.screenshot({ path: shotPath, fullPage: false });

  const report = {
    scenario: SCENARIO,
    frame: FRAME_ARG,
    view: VIEW,
    boot,
    baselineRaf: summarizeGaps(baselineGaps),
    stressPass1: {
      stress: stress.result,
      longTasks: stress.longTasks,
      longTaskCount: stress.longTasks.length,
      longTaskMaxMs: stress.longTasks.reduce(
        (m, x) => Math.max(m, x.ms),
        0,
      ),
      perfSummary: stress.summary,
    },
    stressPass2: {
      stress: during.result,
      raf: summarizeGaps(during.gaps),
      perfSummary: during.summary,
    },
    pageErrors,
    screenshot: shotPath,
  };

  const outFile = resolve(outDir, `${SCENARIO}-${VIEW}-browser.json`);
  await writeFile(outFile, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log("[graph-perf] report", JSON.stringify(report, null, 2));
  console.log("wrote", outFile);

  await browser.close();
  await server.close();

  if (pageErrors.length) {
    console.warn("page errors:", pageErrors);
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
