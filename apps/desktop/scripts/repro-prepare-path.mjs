// Repro of the PRODUCT prepare-path (user types opening message) for demo tape.
// Captures: thinking-state duration at start (bug 1) + whether 授权开赛 appears
// after the case brief (bug 2). Screenshots + per-second probe timeline.
//
//   $env:REPRO_API='http://localhost:8020'; $env:REPRO_SPEED='1'; $env:REPRO_GAP='3000'
//   node scripts/repro-prepare-path.mjs

import { mkdir, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const USER = process.env.REPRO_USER ?? "dev";
const PASS = process.env.REPRO_PASS ?? "devpassword";
const API = process.env.REPRO_API ?? "http://localhost:8020";
const PORT = Number(process.env.REPRO_PORT ?? 5174);
const TAPE = process.env.REPRO_TAPE ?? "lv-molihua-trademark";
const SPEED = Number(process.env.REPRO_SPEED ?? 1);
const GAP = Number(process.env.REPRO_GAP ?? 3000);
// "prepare" = user types opening message (live surfaceResumeFromLiveTurn path);
// "autostart" = 一键开播 /start then attach → GET /recovery surfaces the card.
const MODE = process.env.REPRO_MODE ?? "prepare";
const outDir = resolve(desktopDir, "demo-tape-out");
process.env.VITE_API_URL = API;

const shot = (page, name) =>
  page.screenshot({ path: resolve(outDir, `${name}.png`), fullPage: true }).catch(() => {});

const probe = (page) =>
  page.evaluate(() => {
    const text = (document.body?.innerText ?? "").replace(/\s+/g, " ");
    return {
      thinking: /Thinking…/.test(text),
      delegating: /Composing\s*Delegate|Delegate/.test(text),
      authorize: /授权开赛/.test(text),
      waitKickoff: /等待开工确认|开工卡/.test(text),
      stop: /停止生成/.test(text),
      caseBrief: /案情简介|一审判决/.test(text),
      debate: /主持人|立论|辩题|结辩/.test(text),
      textLen: text.length,
      snippet: text.slice(0, 300),
    };
  });

async function main() {
  process.chdir(desktopDir);
  await mkdir(outDir, { recursive: true });
  const summary = { api: API, mode: MODE, speed: SPEED, gap: GAP, timeline: [], marks: {}, pageErrors: [], ok: false };

  const server = await createServer({
    configFile: resolve(desktopDir, "vite.webapp.config.ts"),
    logLevel: "warn",
    server: { port: PORT, strictPort: true },
  });
  await server.listen();
  const base = server.resolvedUrls?.local?.[0];
  console.log(`webapp ${base} → api ${API} (speed=${SPEED} gap=${GAP})`);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1024, height: 768 }, colorScheme: "light" });
  page.on("pageerror", (e) => summary.pageErrors.push(e.message));
  let csrf = null;
  page.on("response", (r) => {
    const t = r.headers()["x-csrf-token"];
    if (t) csrf = t;
  });

  try {
    await page.goto(new URL("index.webapp.html", base).href, { waitUntil: "load", timeout: 30000 });
    const userBox = page.getByPlaceholder("邮箱或用户名");
    const composer = page.getByPlaceholder(/输入消息/);
    await Promise.race([
      userBox.waitFor({ state: "visible", timeout: 20000 }).catch(() => {}),
      composer.waitFor({ state: "visible", timeout: 20000 }).catch(() => {}),
    ]);
    if (await userBox.isVisible().catch(() => false)) {
      await userBox.fill(USER);
      await page.getByPlaceholder(/密码/).first().fill(PASS);
      await page.locator('button[type="submit"]').click();
    }
    await composer.waitFor({ state: "visible", timeout: 30000 });

    // Launch via API using browser cookies (mirrors palette rows without click flakiness).
    const apiBase = API.replace(/\/$/, "");
    const cookies = await page.context().cookies(apiBase);
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const launch = async (path) => {
      const res = await fetch(`${apiBase}/v1/demo-tape/${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Cookie: cookieHeader,
          ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        },
        body: JSON.stringify({ tape_id: TAPE, speed: SPEED, max_gap_ms: GAP }),
      });
      if (!res.ok) throw new Error(`${path} failed ${res.status}: ${await res.text()}`);
      return res.json();
    };

    let t0;
    if (MODE === "autostart") {
      // 一键开播: /start plays server-side and blocks until the turn pauses; then we
      // navigate and the card must surface via GET /recovery (not a live stream).
      const body = await launch("start");
      const cid = body.conversation_id;
      console.log("autostarted (paused server-side)", cid);
      t0 = Date.now();
      await page.goto(new URL(`index.webapp.html#/conversations/${cid}`, base).href, {
        waitUntil: "load",
        timeout: 30000,
      });
      console.log("navigated to conversation; polling recovery…");
    } else {
      const body = await launch("prepare");
      const cid = body.conversation_id;
      const prompt = body.user_prompt;
      console.log("prepared", cid, JSON.stringify(prompt));
      await page.goto(new URL(`index.webapp.html#/conversations/${cid}`, base).href, {
        waitUntil: "load",
        timeout: 30000,
      });
      await composer.waitFor({ state: "visible", timeout: 20000 });
      await page.waitForTimeout(500);
      // Type + send the opening message (the real user action).
      await composer.click();
      await composer.fill(prompt);
      t0 = Date.now();
      await composer.press("Enter");
      console.log("sent opening message; polling…");
    }

    let firstThinking = null;
    let lastThinking = null;
    let authorizeAt = null;
    let briefAt = null;
    let delegatingAt = null;
    let shotBrief = false;
    let shotDelegating = false;
    for (let i = 0; i < 220; i++) {
      const p = await probe(page);
      const t = Date.now() - t0;
      summary.timeline.push({ t, ...p, snippet: undefined });
      if (p.thinking) {
        if (firstThinking === null) firstThinking = t;
        lastThinking = t;
      }
      if (p.caseBrief && briefAt === null) {
        briefAt = t;
        if (!shotBrief) {
          await shot(page, "repro-01-casebrief");
          shotBrief = true;
        }
      }
      // 案情简介之后、开工卡之前的「正在生成 委派任务 · N 字」——本次修复的目标态。
      if (p.delegating && delegatingAt === null) {
        delegatingAt = t;
        if (!shotDelegating) {
          await shot(page, "repro-015-delegating");
          shotDelegating = true;
        }
      }
      if (p.authorize && authorizeAt === null) {
        authorizeAt = t;
        await shot(page, "repro-02-authorize");
        break;
      }
      await page.waitForTimeout(400);
    }

    // Resume → debate: click 授权开赛 and confirm the debate actually streams (full flow).
    let debateAt = null;
    if (authorizeAt !== null) {
      const btn = page.getByRole("button", { name: "授权开赛" });
      if (await btn.isVisible().catch(() => false)) {
        await btn.click();
        const tResume = Date.now();
        console.log("clicked 授权开赛; polling debate…");
        for (let i = 0; i < 70; i++) {
          const p = await probe(page);
          const t = Date.now() - t0;
          summary.timeline.push({ t, phase: "resume", ...p, snippet: undefined });
          if (p.debate && debateAt === null) {
            debateAt = Date.now() - tResume;
            await shot(page, "repro-04-debate");
            break;
          }
          await page.waitForTimeout(700);
        }
      }
    }
    // Final state shot even if authorize never appeared.
    await shot(page, "repro-03-final");
    const pend = await probe(page);
    summary.marks = {
      firstThinkingMs: firstThinking,
      lastThinkingMs: lastThinking,
      caseBriefMs: briefAt,
      delegatingMs: delegatingAt,
      delegatingAppeared: delegatingAt !== null,
      authorizeMs: authorizeAt,
      authorizeAppeared: authorizeAt !== null,
      debateAfterResumeMs: debateAt,
      debateStreamed: debateAt !== null,
      finalSnippet: pend.snippet,
      finalHasStop: pend.stop,
      finalHasAuthorize: pend.authorize,
      finalWaitKickoff: pend.waitKickoff,
    };
    summary.ok = authorizeAt !== null;
  } catch (err) {
    summary.fatal = String(err?.stack ?? err);
    await shot(page, "repro-99-fatal");
  } finally {
    await browser.close();
    await server.close();
  }

  await writeFile(resolve(outDir, "repro-summary.json"), JSON.stringify(summary, null, 2), "utf8");
  console.log("MARKS", JSON.stringify(summary.marks, null, 2));
  console.log("REPRO", JSON.stringify({ ok: summary.ok, fatal: summary.fatal }));
  process.exitCode = summary.ok ? 0 : 1;
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
