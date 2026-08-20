// Graph viewport probe for offline AI preview (#/preview).
//
// Companion to shoot.mjs: boots the same offline web entry (vite.web.config.ts),
// then measures React Flow viewport metrics across chat-inline /
// fullscreen-graph surfaces (emptyTopRatio, fullyInside, clipped counts,
// viewport transform) and writes screenshots + a JSON report. Use after
// collaboration-graph viewport fixes as a regression harness — measurement
// semantics stay stable; only paths/organization live here.
//
// Usage:
//   node scripts/shoot-graph-probe.mjs
//   node scripts/shoot-graph-probe.mjs multi_agent_debate
//   node scripts/shoot-graph-probe.mjs multi_agent_debate_multibeat multibeat
//   pnpm -C apps/desktop shoot:graph-probe -- multi_agent_debate baseline
//
// Args: [scenario] [outPrefix]
//   scenario  — #/preview scenario name (default multi_agent_debate)
//   outPrefix — optional file prefix so runs don't overwrite (e.g. "multibeat"
//               → multibeat-probe-report.json / multibeat-01-chat-inline.png)
//
// Output: shoot-out-graph-probe/ (gitignored; outside electron-vite out/).

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const SHOOT_OUT_DIR = "shoot-out-graph-probe";
const outDir = resolve(desktopDir, SHOOT_OUT_DIR);
// argv: node shoot-graph-probe.mjs [scenario] [outPrefix]
// 缺省与首跑一致（multi_agent_debate、无前缀）；带 outPrefix 时所有输出文件加前缀，
// 不覆盖已留存的基线 probe-report.json / 截图。
const SCENARIO = process.argv[2] || "multi_agent_debate";
const OUT_PREFIX = process.argv[3] ? `${process.argv[3]}-` : "";
const SETTLE_MS = 2500;

async function probeGraph(page, label) {
  return page.evaluate((probeLabel) => {
    const flows = [...document.querySelectorAll(".react-flow")];
    const containers = flows.map((el) => {
      const r = el.getBoundingClientRect();
      return {
        w: Math.round(r.width),
        h: Math.round(r.height),
        x: Math.round(r.x),
        y: Math.round(r.y),
        bottom: Math.round(r.bottom),
        right: Math.round(r.right),
      };
    });

    const nodes = [...document.querySelectorAll(".react-flow__node")].map(
      (el) => {
        const r = el.getBoundingClientRect();
        const cls = [...el.classList].filter((c) =>
          c.startsWith("react-flow__node-"),
        );
        return {
          id: el.getAttribute("data-id"),
          typeClasses: cls,
          transform: el.style.transform || "",
          bbox: {
            x: Math.round(r.x),
            y: Math.round(r.y),
            w: Math.round(r.width),
            h: Math.round(r.height),
            bottom: Math.round(r.bottom),
          },
          opacity: getComputedStyle(el).opacity,
          visibility: getComputedStyle(el).visibility,
        };
      },
    );

    const viewports = [
      ...document.querySelectorAll(".react-flow__viewport"),
    ].map((el) => ({
      transform: el.style.transform || getComputedStyle(el).transform,
    }));

    const c0 = containers[0];
    const intersecting = nodes.filter((n) => {
      if (!c0) return false;
      const nr = n.bbox.x + n.bbox.w;
      const nb = n.bbox.y + n.bbox.h;
      return (
        nr > c0.x && n.bbox.x < c0.right && nb > c0.y && n.bbox.y < c0.bottom
      );
    });
    const fullyInside = nodes.filter((n) => {
      if (!c0) return false;
      return (
        n.bbox.x >= c0.x &&
        n.bbox.y >= c0.y &&
        n.bbox.x + n.bbox.w <= c0.right &&
        n.bbox.y + n.bbox.h <= c0.bottom
      );
    });
    const clippedBottom = nodes.filter((n) => {
      if (!c0) return false;
      return n.bbox.y < c0.bottom && n.bbox.bottom > c0.bottom;
    });

    const typeCounts = {};
    for (const n of nodes) {
      const t = n.typeClasses[0] ?? "(untyped)";
      typeCounts[t] = (typeCounts[t] ?? 0) + 1;
    }

    // Parse flow-space Y from transform translate(x, y)
    const flowYs = nodes
      .map((n) => {
        const m = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(n.transform);
        return m ? { id: n.id, x: +m[1], y: +m[2] } : null;
      })
      .filter(Boolean);
    const minFlowY = flowYs.length ? Math.min(...flowYs.map((p) => p.y)) : null;
    const maxFlowY = flowYs.length ? Math.max(...flowYs.map((p) => p.y)) : null;

    const card = document.querySelector(".animate-task-card-enter");
    const cardRect = card?.getBoundingClientRect();
    const stripText = (card?.innerText ?? "").slice(0, 400);

    // Empty-looking: container mostly above node cluster
    let emptyTopRatio = null;
    if (c0 && intersecting.length) {
      const topNodeY = Math.min(...intersecting.map((n) => n.bbox.y));
      emptyTopRatio = Math.max(
        0,
        Math.round(((topNodeY - c0.y) / c0.h) * 1000) / 1000,
      );
    }

    return {
      label: probeLabel,
      reactFlowCount: flows.length,
      backgrounds: document.querySelectorAll(".react-flow__background").length,
      containers,
      viewports,
      nodeCount: nodes.length,
      intersectingCount: intersecting.length,
      fullyInsideCount: fullyInside.length,
      clippedBottomCount: clippedBottom.length,
      clippedBottomIds: clippedBottom.map((n) => n.id),
      typeCounts,
      flowYRange: { min: minFlowY, max: maxFlowY },
      emptyTopRatio,
      card: cardRect
        ? {
            w: Math.round(cardRect.width),
            h: Math.round(cardRect.height),
            y: Math.round(cardRect.y),
          }
        : null,
      nodes: nodes.map((n) => ({
        id: n.id,
        typeClasses: n.typeClasses,
        transform: n.transform,
        bbox: n.bbox,
        intersects: intersecting.some((v) => v.id === n.id),
        fullyInside: fullyInside.some((v) => v.id === n.id),
      })),
      stripTextSample: stripText,
      url: location.href,
    };
  }, label);
}

async function shot(page, name) {
  const path = resolve(outDir, `${OUT_PREFIX}${name}`);
  await page.screenshot({ path, fullPage: false });
  return path;
}

async function waitPreview(page, scenario) {
  await page.waitForSelector(
    `[data-preview-scenario="${scenario}"][data-preview-frame="full"]`,
    { timeout: 20_000 },
  );
  await page.evaluate(() => document.fonts?.ready).catch(() => {});
  await page.waitForTimeout(SETTLE_MS);
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
    deviceScaleFactor: 2,
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
  page.on("pageerror", (err) => pageErrors.push(err.message));

  const report = {
    scenario: SCENARIO,
    settleMs: SETTLE_MS,
    surfaces: {},
    midframes: {},
  };

  // 1) Chat
  {
    const url = new URL("index.web.html", base);
    url.hash = `/preview?s=${SCENARIO}`;
    console.log("CHAT", url.href);
    await page.goto(url.href, { waitUntil: "load", timeout: 30_000 });
    await waitPreview(page, SCENARIO);
    report.surfaces.chat = await probeGraph(page, "chat-inline");
    report.surfaces.chat.screenshot = await shot(page, "01-chat-inline.png");
    const card = page.locator(".animate-task-card-enter").first();
    if (await card.count()) {
      await card.screenshot({
        path: resolve(outDir, `${OUT_PREFIX}01b-chat-card.png`),
      });
    }
    // Also crop the react-flow pane
    const rf = page.locator(".react-flow").first();
    if (await rf.count()) {
      await rf.screenshot({
        path: resolve(outDir, `${OUT_PREFIX}01c-chat-reactflow.png`),
      });
    }
  }

  // Mid-stream frames: look for layoutReady=false (no .react-flow).
  // Non-fatal per frame — a scenario may have fewer frames than k.
  for (const k of [8, 15, 22]) {
    try {
      const url = new URL("index.web.html", base);
      url.hash = `/preview?s=${SCENARIO}&k=${k}`;
      await page.goto(url.href, { waitUntil: "load", timeout: 30_000 });
      await page.waitForSelector(
        `[data-preview-scenario="${SCENARIO}"][data-preview-frame="${k}"]`,
        { timeout: 15_000 },
      );
      await page.waitForTimeout(SETTLE_MS);
      const mid = await probeGraph(page, `chat-k${k}`);
      mid.pageErrors = [...pageErrors];
      pageErrors.length = 0;
      report.midframes[`k${k}`] = {
        reactFlowCount: mid.reactFlowCount,
        nodeCount: mid.nodeCount,
        typeCounts: mid.typeCounts,
        emptyTopRatio: mid.emptyTopRatio,
        intersectingCount: mid.intersectingCount,
        pageErrors: mid.pageErrors,
      };
      await shot(page, `01-mid-k${k}.png`);
    } catch (err) {
      report.midframes[`k${k}`] = { error: String(err?.message ?? err) };
    }
  }

  // 2) Fullscreen graph
  {
    const url = new URL("index.web.html", base);
    url.hash = `/preview?s=${SCENARIO}`;
    await page.goto(url.href, { waitUntil: "load", timeout: 30_000 });
    await waitPreview(page, SCENARIO);
    const openDebate = page.getByRole("button", { name: "打开辩论室" });
    const openCanvas = page.getByRole("button", { name: "在画布打开" });
    if (await openDebate.count()) await openDebate.first().click();
    else if (await openCanvas.count()) await openCanvas.first().click();
    await page.waitForTimeout(1200);
    const graphTab = page.getByRole("button", { name: "协作图" });
    if (await graphTab.count()) {
      await graphTab.first().click();
      await page.waitForTimeout(SETTLE_MS);
    }
    report.surfaces.fullscreen = await probeGraph(page, "fullscreen-graph");
    report.surfaces.fullscreen.screenshot = await shot(
      page,
      "02-fullscreen-graph.png",
    );
  }

  function classify(s) {
    if (!s) return { notes: ["no-data"] };
    const notes = [];
    if (s.reactFlowCount === 0) {
      notes.push("c: no .react-flow → layoutReady false or graph not mounted");
    }
    const agents = s.typeCounts?.["react-flow__node-agent"] ?? 0;
    const groups =
      (s.typeCounts?.["react-flow__node-subTeamGroup"] ?? 0) +
      (s.typeCounts?.["react-flow__node-turnGroup"] ?? 0);
    if (s.reactFlowCount > 0 && agents === 0 && groups > 0) {
      notes.push("b: groups only, agents skipped (positions miss)");
    }
    if (agents > 0) notes.push(`not-b: ${agents} agent nodes mounted`);
    if (s.reactFlowCount > 0)
      notes.push("not-c: ReactFlow mounted (layoutReady true)");
    if (s.emptyTopRatio != null && s.emptyTopRatio > 0.35) {
      notes.push(
        `a: emptyTopRatio=${s.emptyTopRatio} — content cluster sits low; upper pane looks like empty dots`,
      );
    }
    if (s.clippedBottomCount > 0) {
      notes.push(
        `a: ${s.clippedBottomCount} nodes clipped by container bottom (overflow-hidden): ${s.clippedBottomIds?.join(",")}`,
      );
    }
    if (
      s.flowYRange?.min != null &&
      s.flowYRange.min > 200 &&
      /translate\(0px,\s*0px\)/.test(s.viewports?.[0]?.transform ?? "")
    ) {
      notes.push(
        `a: flowY min=${s.flowYRange.min} but viewport y=0 (fitMode=width does not recenter like fitView)`,
      );
    }
    return { notes };
  }

  report.classification = {
    chat: classify(report.surfaces.chat),
    fullscreen: classify(report.surfaces.fullscreen),
    midframes: report.midframes,
  };

  report.pageErrorsTail = [...pageErrors];
  const reportPath = resolve(outDir, `${OUT_PREFIX}probe-report.json`);
  await writeFile(reportPath, JSON.stringify(report, null, 2), "utf8");
  console.log(JSON.stringify(report.classification, null, 2));
  console.log("Wrote", reportPath);

  await browser.close();
  await server.close();
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
