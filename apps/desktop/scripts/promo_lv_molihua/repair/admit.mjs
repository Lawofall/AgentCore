/**
 * repair --preset admit — B-field R3 admission close-up (07 + 07b).
 */
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { preview } from "vite";
import { desktopDir, resolveCapturePaths } from "../shared/paths.mjs";

let outRoot;
let stillsDir;
let API;
let PORT;
let USER;
let PASS;
let TAPE;
const ADMIT = "我承认没有消费者调查数据支撑";
const ADMIT_T_MS = 1_130_562;

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
    return;
  }
  const open = page.getByRole("button", { name: /打开辩论室/ });
  if (await open.first().isVisible().catch(() => false)) {
    await open.first().click();
    await page.waitForTimeout(1200);
  }
}

function authHeaders(cookieHeader, csrf) {
  return {
    "Content-Type": "application/json",
    Cookie: cookieHeader,
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
  };
}

async function director(api, cookieHeader, csrf, cid, method, path, body) {
  const url = `${api}/v1/demo-tape/director/${cid}${path}`;
  const res = await fetch(url, {
    method,
    headers: authHeaders(cookieHeader, csrf),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    /* plain */
  }
  if (!res.ok) throw new Error(`director ${method} ${path} → ${res.status}: ${text.slice(0, 300)}`);
  return json;
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

    // Fast path: prepare + autostart-style message, then director seek past authorize
    const prep = await fetch(`${API}/v1/demo-tape/prepare`, {
      method: "POST",
      headers: authHeaders(cookieHeader, csrf),
      body: JSON.stringify({ tape_id: TAPE, speed: 8, max_gap_ms: 600 }),
    });
    if (!prep.ok) throw new Error(await prep.text());
    const { conversation_id: cid, user_prompt: prompt } = await prep.json();
    report.notes.push(`cid=${cid}`);

    await page.goto(new URL(`index.webapp.html#/conversations/${cid}`, base).href, {
      waitUntil: "load",
      timeout: 30_000,
    });
    await composer.waitFor({ state: "visible", timeout: 20_000 });
    await dismissOnboarding(page);
    await composer.fill(prompt || "搜索最新的LV起诉茉莉奶白这个案件、简单向我介绍之后启动模拟庭审辩论");
    await composer.press("Enter");

    for (let i = 0; i < 90; i++) {
      const auth = page.getByRole("button", { name: /授权开赛|授权并开工/ });
      if (await auth.first().isVisible().catch(() => false)) {
        await auth.first().click();
        report.notes.push("authorized");
        break;
      }
      await page.waitForTimeout(400);
    }

    // Wait until director sees a session, then seek to admit
    for (let i = 0; i < 40; i++) {
      try {
        const st = await director(API, cookieHeader, csrf, cid, "GET", "/status");
        if (st && (st.state || st.t_ms != null)) break;
      } catch {
        /* not ready */
      }
      await page.waitForTimeout(500);
    }

    await director(API, cookieHeader, csrf, cid, "POST", "/speed", { speed: 8 });
    await director(API, cookieHeader, csrf, cid, "POST", "/seek", { t_ms: ADMIT_T_MS });
    for (let i = 0; i < 60; i++) {
      const st = await director(API, cookieHeader, csrf, cid, "GET", "/status");
      if (Number(st.t_ms) >= ADMIT_T_MS - 5000) {
        report.notes.push(`seek ok t_ms=${st.t_ms}`);
        break;
      }
      await page.waitForTimeout(500);
    }

    // Hard reload so DB fold lands; debate room may need a click
    await page.goto(new URL(`index.webapp.html#/conversations/${cid}`, base).href, {
      waitUntil: "load",
      timeout: 30_000,
    });
    await page.waitForTimeout(1500);
    await ensureDebateRoom(page);
    const r3 = page.getByRole("button", { name: /第\s*3\s*轮/ });
    if (await r3.first().isVisible().catch(() => false)) {
      await r3.first().click().catch(() => {});
      await page.waitForTimeout(700);
    }

    let body = "";
    let ctx = null;
    for (let i = 0; i < 30; i++) {
      body = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
      if (body.includes(ADMIT)) {
        ctx = await page.evaluate((needle) => {
          const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = walk.nextNode())) {
            if (node.textContent && node.textContent.includes(needle)) {
              const el = node.parentElement;
              el?.scrollIntoView({ block: "center", inline: "nearest" });
              return (el?.innerText || node.textContent).slice(0, 260);
            }
          }
          return null;
        }, ADMIT);
        break;
      }
      // Expand 展开全文 if present
      const expand = page.getByRole("button", { name: /展开全文/ });
      const n = await expand.count();
      for (let j = 0; j < Math.min(n, 6); j++) {
        await expand.nth(j).click().catch(() => {});
      }
      await page.waitForTimeout(600);
    }

    if (!body.includes(ADMIT)) {
      throw new Error(`admit not in UI after seek; body slice=${body.slice(0, 200)}`);
    }

    await page.waitForTimeout(400);
    const path07 = resolve(stillsDir, "07-evidence-gap-admit.png");
    const path07b = resolve(stillsDir, "07b-admit-closeup.png");
    await page.screenshot({ path: path07, type: "png" });
    await page.screenshot({ path: path07b, type: "png" });
    report.shots.push({ id: "07-evidence-gap-admit", path: path07, ctx });
    report.shots.push({ id: "07b-admit-closeup", path: path07b, ctx });
    report.ok = Boolean(ctx && ctx.includes(ADMIT));
    report.notes.push(`quote_visible=${report.ok}; ctx=${(ctx || "").slice(0, 120)}`);

    // Patch manifest.json
    try {
      const manPath = resolve(outRoot, "manifest.json");
      const man = JSON.parse(await readFile(manPath, "utf8"));
      const assets = man.assets || [];
      for (const shot of report.shots) {
        const entry = {
          id: shot.id,
          file: `stills/${shot.id}.png`,
          path: shot.path,
          label:
            shot.id === "07b-admit-closeup"
              ? "质询承认句特写"
              : "质询高光 · LV 承认无消费者调查",
          usage:
            shot.id === "07b-admit-closeup"
              ? "交锋3 全片最强镜头；须可读承认句原话"
              : "交锋3 质询高光",
          tape_t_ms: ADMIT_T_MS,
          clean: true,
          new: shot.id === "07b-admit-closeup",
          quote_required:
            "我承认没有消费者调查数据支撑「茶饮消费者看到四叶花联想到LV」的主张",
          matched_text: (shot.ctx || ADMIT).slice(0, 160),
        };
        const idx = assets.findIndex((a) => a.id === shot.id);
        if (idx >= 0) assets[idx] = { ...assets[idx], ...entry };
        else assets.push(entry);
      }
      man.assets = assets;
      man.notes = man.notes || [];
      man.notes.push(`admit-capture ${new Date().toISOString()}: ok=${report.ok}`);
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
    resolve(outRoot, "admit-capture-report.json"),
    JSON.stringify(report, null, 2),
    "utf8",
  );
  console.log("ADMIT", JSON.stringify({ ok: report.ok, shots: report.shots.map((s) => s.id), notes: report.notes }));
  process.exitCode = report.ok ? 0 : 1;
}

/**
 * @param {{ tape?: string, out?: string }} opts
 */
export async function run(opts = {}) {
  const paths = resolveCapturePaths(opts);
  outRoot = paths.outRoot;
  stillsDir = paths.stillsDir;
  TAPE = paths.tape;
  USER = process.env.PROMO_USER ?? "promo_lv";
  PASS = process.env.PROMO_PASS ?? "promopass";
  API = (process.env.PROMO_API ?? "http://localhost:8015").replace(/\/$/, "");
  PORT = Number(process.env.PROMO_PORT ?? 5174);
  process.env.VITE_API_URL = API;
  await main();
}
