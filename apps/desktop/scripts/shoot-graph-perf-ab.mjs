// A/B: canvas stream every rAF vs every 5th rAF — isolates RF reconcile jank.
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const desktopDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = resolve(desktopDir, "shoot-out-graph-perf");

function summarize(gaps) {
  const s = [...gaps].sort((a, b) => a - b);
  const pct = (p) =>
    s[
      Math.min(s.length - 1, Math.max(0, Math.ceil((p / 100) * s.length) - 1))
    ] ?? 0;
  return {
    n: gaps.length,
    p50: +pct(50).toFixed(1),
    p95: +pct(95).toFixed(1),
    max: +((s[s.length - 1] ?? 0).toFixed(1)),
    over20: gaps.filter((v) => v > 20).length,
    over33: gaps.filter((v) => v > 33).length,
  };
}

async function main() {
  process.chdir(desktopDir);
  await mkdir(outDir, { recursive: true });
  const server = await createServer({
    configFile: resolve(desktopDir, "vite.web.config.ts"),
    logLevel: "warn",
  });
  await server.listen();
  const base = server.resolvedUrls?.local?.[0];
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });

  async function runAB(everyN) {
    const url = new URL("index.web.html", base);
    url.hash = "/preview?s=multi_agent_debate_multibeat";
    await page.goto(url.href, { waitUntil: "load", timeout: 60_000 });
    await page.waitForSelector(
      '[data-preview-scenario="multi_agent_debate_multibeat"][data-preview-frame="full"]',
      { timeout: 30_000 },
    );
    await page.waitForTimeout(2000);
    await page.evaluate(() => {
      window.__graphPerf?.(true);
      window.__graphPerf?.clear?.();
    });
    const result = await page.evaluate(async (everyNFrames) => {
      const gaps = [];
      let last = performance.now();
      const start = last;
      const ms = 3000;
      const stressP = window.__graphStress({ durationMs: ms, everyNFrames });
      await new Promise((resolve) => {
        const step = (now) => {
          gaps.push(now - last);
          last = now;
          if (now - start >= ms) resolve();
          else requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      });
      const stress = await stressP;
      return {
        gaps,
        stress,
        nodes: document.querySelectorAll(".react-flow__node").length,
        particles: document.querySelectorAll("animateMotion").length,
        summary: window.__graphPerf?.summary?.(),
      };
    }, everyN);
    return {
      everyN,
      raf: summarize(result.gaps),
      stress: result.stress,
      nodes: result.nodes,
      particles: result.particles,
      perf: result.summary,
    };
  }

  const everyFrame = await runAB(1);
  const every5 = await runAB(5);
  const report = {
    ab: "canvas debate multibeat stream throttle",
    everyFrame,
    every5Frames: every5,
  };
  const out = resolve(outDir, "canvas-throttle-ab.json");
  await writeFile(out, `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
  console.log("wrote", out);
  await browser.close();
  await server.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
