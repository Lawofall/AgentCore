/**
 * repair --preset fixup — scroll-into-view quote / admit / verdict stills.
 */
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { preview } from "vite";
import { desktopDir, resolveCapturePaths } from "../shared/paths.mjs";

let TAPE;
let outRoot;
let stillsDir;
let API;
let PORT;
let USER;
let PASS;
let SPEED;
let GAP;
const QUOTE = "任何经营者都不能垄断自然界公共资源的基本表达";
/** B 场全片最强镜头：LV 质询承认句（磁带 debate_round r3 @ t_ms≈1130562） */
const ADMIT = "我承认没有消费者调查数据支撑";

async function dismissOnboarding(page) {
  const dialog = page.locator('[aria-label="欢迎使用 AgentCore"]');
  if (!(await dialog.isVisible().catch(() => false))) return;
  const skip = dialog.getByRole("button", { name: /^跳过$/ });
  if (await skip.isVisible().catch(() => false)) await skip.click();
  await dialog.waitFor({ state: "hidden", timeout: 10_000 }).catch(() => {});
}

async function ensureDebateRoom(page) {
  const debateTab = page.getByRole("button", { name: /^辩论室$/ });
  if (await debateTab.isVisible().catch(() => false)) {
    await debateTab.click();
    await page.waitForTimeout(500);
  } else {
    const open = page.getByRole("button", { name: /打开辩论室/ });
    if (await open.first().isVisible().catch(() => false)) {
      await open.first().click();
      await page.waitForTimeout(1000);
    }
  }
}

async function scrollQuoteIntoView(page) {
  return page.evaluate((quote) => {
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walk.nextNode())) {
      if (
        node.textContent &&
        (node.textContent.includes(quote) ||
          node.textContent.includes("垄断自然界公共资源"))
      ) {
        const el = node.parentElement;
        if (el) {
          el.scrollIntoView({ block: "center", inline: "nearest" });
          return el.innerText.slice(0, 200);
        }
      }
    }
    const all = [...document.querySelectorAll("p, li, div, span")];
    for (const el of all) {
      const t = el.innerText || "";
      if (t.includes(quote) || t.includes("垄断自然界公共资源")) {
        el.scrollIntoView({ block: "center" });
        return t.slice(0, 200);
      }
    }
    return null;
  }, QUOTE);
}

async function scrollVerdictIntoView(page) {
  return page.evaluate(() => {
    const all = [...document.querySelectorAll("p, li, div, span, h1, h2, h3")];
    for (const el of all) {
      const t = el.innerText || "";
      if (
        t.includes("微弱倾向茉莉奶白") ||
        t.includes("倾向茉莉奶白") ||
        (t.includes("55%") && t.includes("倾向"))
      ) {
        el.scrollIntoView({ block: "center" });
        return t.slice(0, 200);
      }
    }
    for (const el of all) {
      if (
        (el.innerText || "").includes("微弱倾向茉莉奶白") ||
        (el.innerText || "").includes("倾向茉莉奶白")
      ) {
        el.scrollIntoView({ block: "center" });
        return el.innerText.slice(0, 200);
      }
    }
    return null;
  });
}

async function scrollAdmitIntoView(page) {
  return page.evaluate((needle) => {
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walk.nextNode())) {
      if (node.textContent && node.textContent.includes(needle)) {
        const el = node.parentElement;
        if (el) {
          el.scrollIntoView({ block: "center", inline: "nearest" });
          return el.innerText.slice(0, 240);
        }
      }
    }
    const all = [...document.querySelectorAll("p, li, div, span")];
    for (const el of all) {
      const t = el.innerText || "";
      if (t.includes(needle)) {
        el.scrollIntoView({ block: "center" });
        return t.slice(0, 240);
      }
    }
    return null;
  }, ADMIT);
}

async function main() {
  process.chdir(desktopDir);
  await mkdir(stillsDir, { recursive: true });
  const report = { ok: false, shots: [], notes: [] };

  const server = await preview({
    configFile: resolve(desktopDir, "vite.webapp.config.ts"),
    preview: { port: PORT, strictPort: true },
  });
  const base = server.resolvedUrls.local[0];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    colorScheme: "light",
    locale: "zh-CN",
  });
  let csrf = null;
  page.on("response", (r) => {
    const t = r.headers()["x-csrf-token"];
    if (t) csrf = t;
  });

  try {
    await page.goto(new URL("index.webapp.html", base).href, {
      waitUntil: "load",
      timeout: 30_000,
    });
    const userBox = page.getByPlaceholder("邮箱或用户名");
    const composer = page.getByPlaceholder(/输入消息/);
    await Promise.race([
      userBox.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
      composer.waitFor({ state: "visible", timeout: 20_000 }).catch(() => {}),
    ]);
    if (await userBox.isVisible().catch(() => false)) {
      await userBox.fill(USER);
      await page.getByPlaceholder(/密码/).first().fill(PASS);
      await page.locator('button[type="submit"]').click();
    }
    await composer.waitFor({ state: "visible", timeout: 30_000 });
    await dismissOnboarding(page);

    const cookies = await page.context().cookies(API);
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const headers = {
      "Content-Type": "application/json",
      Cookie: cookieHeader,
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    };

    const prep = await fetch(`${API}/v1/demo-tape/prepare`, {
      method: "POST",
      headers,
      body: JSON.stringify({ tape_id: TAPE, speed: SPEED, max_gap_ms: GAP }),
    });
    if (!prep.ok) throw new Error(await prep.text());
    const { conversation_id: cid, user_prompt: prompt } = await prep.json();
    await page.goto(new URL(`index.webapp.html#/conversations/${cid}`, base).href, {
      waitUntil: "load",
      timeout: 30_000,
    });
    await composer.waitFor({ state: "visible", timeout: 20_000 });
    await dismissOnboarding(page);
    await composer.fill(
      prompt || "搜索最新的LV起诉茉莉奶白这个案件、简单向我介绍之后启动模拟庭审辩论",
    );
    await composer.press("Enter");

    // wait authorize + click
    for (let i = 0; i < 120; i++) {
      const auth = page.getByRole("button", { name: /授权开赛|授权并开工/ });
      if (await auth.first().isVisible().catch(() => false)) {
        await auth.first().click();
        break;
      }
      await page.waitForTimeout(400);
    }

    let quoteDone = false;
    let admitDone = false;
    let verdictDone = false;
    const deadline = Date.now() + 12 * 60_000;

    while (Date.now() < deadline && (!quoteDone || !admitDone || !verdictDone)) {
      await ensureDebateRoom(page);
      const body = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));

      if (
        !quoteDone &&
        (body.includes("垄断自然界公共资源") || body.includes("任何经营者都不能垄断"))
      ) {
        const ctx = await scrollQuoteIntoView(page);
        await page.waitForTimeout(400);
        const path = resolve(stillsDir, "04b-r2-quote-closeup.png");
        await page.screenshot({ path, type: "png" });
        // also refresh 04
        await page.screenshot({
          path: resolve(stillsDir, "04-r2-diamond-square.png"),
          type: "png",
        });
        report.shots.push({ id: "04b-r2-quote-closeup", path, ctx });
        report.shots.push({
          id: "04-r2-diamond-square",
          path: resolve(stillsDir, "04-r2-diamond-square.png"),
          ctx,
        });
        quoteDone = true;
        console.log("SHOT 04b", ctx?.slice(0, 80));
      }

      if (!admitDone && body.includes(ADMIT)) {
        const r3 = page.getByRole("button", { name: /第\s*3\s*轮/ });
        if (await r3.first().isVisible().catch(() => false)) {
          await r3.first().click().catch(() => {});
          await page.waitForTimeout(500);
        }
        const ctx = await scrollAdmitIntoView(page);
        await page.waitForTimeout(400);
        const path07 = resolve(stillsDir, "07-evidence-gap-admit.png");
        await page.screenshot({ path: path07, type: "png" });
        const path07b = resolve(stillsDir, "07b-admit-closeup.png");
        await page.screenshot({ path: path07b, type: "png" });
        report.shots.push({ id: "07-evidence-gap-admit", path: path07, ctx });
        report.shots.push({ id: "07b-admit-closeup", path: path07b, ctx });
        admitDone = true;
        console.log("SHOT 07/07b", ctx?.slice(0, 100));
      }

      if (
        !verdictDone &&
        /微弱倾向茉莉奶白|倾向茉莉奶白/.test(body) &&
        /置信|55|定夺|符号独占/.test(body)
      ) {
        // click 结辩 / 终审 chip if present (B-field UI uses 结辩)
        const verdictChip = page.getByRole("button", { name: /结辩|终审|裁决|决策简报/ });
        if (await verdictChip.first().isVisible().catch(() => false)) {
          await verdictChip.first().click().catch(() => {});
          await page.waitForTimeout(800);
        }
        const ctx = await scrollVerdictIntoView(page);
        await page.waitForTimeout(400);
        const path = resolve(stillsDir, "08-final-verdict.png");
        await page.screenshot({ path, type: "png" });
        report.shots.push({ id: "08-final-verdict", path, ctx });
        verdictDone = true;
        console.log("SHOT 08", ctx?.slice(0, 80));
      }

      await page.waitForTimeout(500);
    }

    report.ok = quoteDone && admitDone && verdictDone;
    report.notes.push(
      `quoteDone=${quoteDone} admitDone=${admitDone} verdictDone=${verdictDone}`,
    );

    // patch MANIFEST notes
    try {
      const manPath = resolve(outRoot, "manifest.json");
      const man = JSON.parse(await readFile(manPath, "utf8"));
      man.fixup = report;
      man.notes = man.notes || [];
      man.notes.push(
        `fixup ${new Date().toISOString()}: quote=${quoteDone} admit=${admitDone} verdict=${verdictDone}`,
      );
      await writeFile(manPath, JSON.stringify(man, null, 2), "utf8");
    } catch (e) {
      report.notes.push(String(e));
    }
  } catch (e) {
    report.fatal = String(e?.stack || e);
    console.error(report.fatal);
  } finally {
    await browser.close();
    await server.close();
  }

  await writeFile(
    resolve(outRoot, "fixup-report.json"),
    JSON.stringify(report, null, 2),
    "utf8",
  );
  console.log("FIXUP", JSON.stringify({ ok: report.ok, shots: report.shots.map((s) => s.id) }));
  process.exitCode = report.ok ? 0 : 1;
}

/**
 * @param {{ tape?: string, out?: string }} opts
 */
export async function run(opts = {}) {
  const paths = resolveCapturePaths(opts);
  TAPE = paths.tape;
  outRoot = paths.outRoot;
  stillsDir = paths.stillsDir;
  USER = process.env.PROMO_USER ?? "promo_lv";
  PASS = process.env.PROMO_PASS ?? "promopass";
  API = (process.env.PROMO_API ?? "http://localhost:8015").replace(/\/$/, "");
  PORT = Number(process.env.PROMO_PORT ?? 5174);
  SPEED = Number(process.env.PROMO_SPEED ?? 16);
  GAP = Number(process.env.PROMO_GAP ?? 500);
  process.env.VITE_API_URL = API;
  await main();
}
