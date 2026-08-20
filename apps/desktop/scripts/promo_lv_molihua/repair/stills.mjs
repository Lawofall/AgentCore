/**
 * repair --preset stills — content-gated still repair.
 * Invoked via: node scripts/promo_capture_lv_molihua.mjs repair [--only …]
 */
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { preview } from "vite";
import { authHeaders, director, waitStatus } from "../shared/director.mjs";
import { desktopDir, resolveCapturePaths } from "../shared/paths.mjs";
import {
  dismissOnboarding,
  ensureDebateRoom,
  needlesHit,
  scrollNeedle,
} from "../shared/ui.mjs";

let outRoot;
let stillsDir;
let API;
let PORT;
let USER;
let PASS;
let TAPE;

const QUOTE = "任何经营者都不能垄断自然界公共资源的基本表达";
const VERDICT = "微弱倾向茉莉奶白";

const ALL_JOBS = [
  {
    id: "04-r2-diamond-square",
    // Chapter seek right after authorize often lands before R1 speeches hydrate;
    // pin mid-R1 wall-clock where the public-domain quote is on tape.
    t_ms: 450_000,
    round: 1,
    needles: ["垄断自然界公共资源", "获得显著性", "唯一关联", "固有显著性", "公共资源"],
    scroll: QUOTE,
  },
  {
    id: "04b-r2-quote-closeup",
    t_ms: 450_000,
    round: 1,
    needles: [QUOTE, "垄断自然界公共资源"],
    scroll: QUOTE,
    requireAnyStrict: [QUOTE],
  },
  {
    id: "05-r3-logo-swap",
    chapter_id: "r2_argument",
    round: 2,
    needles: ["跨类", "第43类", "真实商业使用", "防御注册", "茶饮消费者"],
  },
  {
    id: "05b-r4-logo-defense",
    chapter_id: "r3_argument",
    round: 3,
    needles: ["消费者调查", "反稀释", "相当程度的联系", "实证门槛"],
  },
  {
    id: "08-final-verdict",
    chapter_id: "verdict",
    needles: [VERDICT, "倾向茉莉奶白", "55%"],
    chip: /终审|裁决|结辩|决策简报/,
    scroll: VERDICT,
    requireAnyStrict: [VERDICT],
  },
];

function parseOnly(onlyArg) {
  if (!onlyArg) return null;
  return new Set(
    String(onlyArg)
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  );
}

async function land(page, base, cid, job) {
  await page.goto(new URL(`index.webapp.html#/conversations/${cid}`, base).href, {
    waitUntil: "load",
    timeout: 30_000,
  });
  await page.waitForTimeout(1500);
  await dismissOnboarding(page);
  await ensureDebateRoom(page);
  if (job.round) {
    const chip = page.getByRole("button", {
      name: new RegExp(`第\\s*${job.round}\\s*轮`),
    });
    if (await chip.first().isVisible().catch(() => false)) {
      await chip.first().click().catch(() => {});
      await page.waitForTimeout(700);
    }
    await page.evaluate((round) => {
      const header = document.getElementById(`debate-round-${round}`);
      header?.parentElement?.scrollIntoView({ block: "start" });
    }, job.round);
  }
  if (job.chip) {
    const chip = page.getByRole("button", { name: job.chip });
    if (await chip.first().isVisible().catch(() => false)) {
      await chip.first().click().catch(() => {});
      await page.waitForTimeout(800);
    }
  }
  const expand = page.getByRole("button", { name: /展开全文/ });
  const n = await expand.count();
  for (let j = 0; j < Math.min(n, 12); j++) {
    await expand.nth(j).click().catch(() => {});
  }
  if (n > 0) await page.waitForTimeout(400);
}

async function main(onlyArg) {
  process.chdir(desktopDir);
  await mkdir(stillsDir, { recursive: true });
  const only = parseOnly(onlyArg);
  const jobs = only ? ALL_JOBS.filter((j) => only.has(j.id)) : ALL_JOBS;
  const report = { ok: [], miss: [], notes: [], fatal: null };

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
    await composer.fill(
      prompt || "搜索最新的LV起诉茉莉奶白这个案件、简单向我介绍之后启动模拟庭审辩论",
    );
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

    // Repair mid-debate stills before verdict (verdict seek can unbind tape).
    const mid = jobs.filter((j) => j.id !== "08-final-verdict");
    const end = jobs.filter((j) => j.id === "08-final-verdict");

    for (const job of [...mid, ...end]) {
      console.log("→", job.id);
      try {
        if (job.t_ms != null) {
          await director(API, cookieHeader, csrf, cid, "POST", "/seek", {
            t_ms: job.t_ms,
          });
          await waitStatus(
            API,
            cookieHeader,
            csrf,
            cid,
            (s) => Number(s.t_ms) >= job.t_ms - 5000,
            { timeoutMs: 120_000, label: `seek t_ms=${job.t_ms}` },
          );
        } else {
          await director(API, cookieHeader, csrf, cid, "POST", "/seek", {
            chapter_id: job.chapter_id,
          });
          await waitStatus(
            API,
            cookieHeader,
            csrf,
            cid,
            (s) =>
              Number(s.t_ms) > 0 ||
              (s.chapter_label || "").length > 0 ||
              String(s.state).toLowerCase().includes("finish"),
            { timeoutMs: 120_000, label: `seek ${job.chapter_id}` },
          );
        }
        await land(page, base, cid, job);

        let text = "";
        let ctx = null;
        for (let i = 0; i < 30; i++) {
          text = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
          if (needlesHit(text, job)) {
            if (job.scroll) ctx = await scrollNeedle(page, job.scroll);
            break;
          }
          if (job.round) {
            await page.evaluate((round) => {
              const section = document.getElementById(`debate-round-${round}`)?.parentElement;
              section?.querySelectorAll("button").forEach((b) => {
                if (/展开全文/.test(b.textContent || "")) b.click();
              });
            }, job.round);
          }
          if (job.chip) {
            const chip = page.getByRole("button", { name: job.chip });
            if (await chip.first().isVisible().catch(() => false)) {
              await chip.first().click().catch(() => {});
            }
          }
          const expand = page.getByRole("button", { name: /展开全文/ });
          const n = await expand.count();
          for (let j = 0; j < Math.min(n, 8); j++) {
            await expand.nth(j).click().catch(() => {});
          }
          await page.waitForTimeout(500);
        }

        if (!needlesHit(text, job)) {
          report.miss.push({
            id: job.id,
            reason: `needles miss; snip=${text.slice(0, 140)}`,
          });
          console.error("MISS", job.id);
          continue;
        }
        if (job.scroll && !(ctx || text).includes(job.scroll)) {
          report.miss.push({
            id: job.id,
            reason: `scroll needle「${job.scroll}」not in view; snip=${text.slice(0, 140)}`,
          });
          console.error("MISS scroll", job.id);
          continue;
        }

        const path = resolve(stillsDir, `${job.id}.png`);
        await page.screenshot({ path, type: "png" });
        report.ok.push({ id: job.id, path, ctx: (ctx || "").slice(0, 160) });
        console.log("OK", job.id);
      } catch (e) {
        report.miss.push({ id: job.id, reason: String(e.message || e) });
        console.error("ERR", job.id, e);
      }
    }
  } catch (e) {
    report.fatal = String(e?.stack || e);
    console.error(report.fatal);
  } finally {
    await browser.close();
    await server.close();
  }

  await writeFile(
    resolve(outRoot, "repair-stills-report.json"),
    JSON.stringify(report, null, 2),
    "utf8",
  );
  console.log(
    "REPAIR",
    JSON.stringify({
      ok: report.ok.map((x) => x.id),
      miss: report.miss.map((m) => m.id),
      fatal: report.fatal,
    }),
  );
  process.exitCode = report.fatal || report.miss.length || report.ok.length === 0 ? 1 : 0;
}

/**
 * @param {{ tape?: string, out?: string, only?: string }} opts
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
  await main(opts.only);
}
