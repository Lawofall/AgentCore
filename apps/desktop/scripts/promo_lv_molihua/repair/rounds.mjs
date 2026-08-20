/**
 * repair --preset rounds — scoreboard「第N轮」定点覆写 04/05/05b/06/07.
 */
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { preview } from "vite";
import { desktopDir, resolveCapturePaths } from "../shared/paths.mjs";

let stillsDir;
let API;
let PORT;
let SPEED;
let GAP;
let TAPE;

const JOBS = [
  {
    id: "04-r2-diamond-square",
    round: 1,
    needles: ["垄断自然界公共资源", "获得显著性", "唯一关联", "固有显著性"],
    anyOf: true,
  },
  {
    id: "05-r3-logo-swap",
    round: 2,
    needles: ["跨类", "第43类", "真实商业使用", "防御注册", "茶饮消费者"],
    anyOf: true,
  },
  {
    id: "05b-r4-logo-defense",
    round: 3,
    needles: ["消费者调查", "反稀释", "相当程度的联系", "实证门槛"],
    anyOf: true,
  },
  {
    id: "06-r5-burden",
    round: 4,
    needles: ["确实无法提供茶饮消费者", "实证调查", "实际使用前提", "罚分"],
    anyOf: true,
  },
  {
    id: "07-evidence-gap-admit",
    round: 3,
    needles: ["我承认没有消费者调查", "没有消费者调查数据支撑", "确实无法提供茶饮消费者"],
    anyOf: true,
  },
];

async function main(onlyArg) {
  process.chdir(desktopDir);
  await mkdir(stillsDir, { recursive: true });
  const only = onlyArg
    ? new Set(
        String(onlyArg)
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      )
    : null;
  const jobs = only ? JOBS.filter((j) => only.has(j.id)) : JOBS;
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
  const result = { ok: [], miss: [], fatal: null };

  try {
    await page.goto(new URL("index.webapp.html", base).href, { waitUntil: "load", timeout: 30000 });
    const userBox = page.getByPlaceholder("邮箱或用户名");
    const composer = page.getByPlaceholder(/输入消息/);
    await Promise.race([
      userBox.waitFor({ state: "visible", timeout: 20000 }).catch(() => {}),
      composer.waitFor({ state: "visible", timeout: 20000 }).catch(() => {}),
    ]);
    if (await userBox.isVisible().catch(() => false)) {
      await userBox.fill(process.env.PROMO_USER ?? "promo_lv");
      await page.getByPlaceholder(/密码/).first().fill(process.env.PROMO_PASS ?? "promopass");
      await page.locator('button[type="submit"]').click();
    }
    await composer.waitFor({ state: "visible", timeout: 30000 });
    const cookies = await page.context().cookies(API);
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const startRes = await fetch(`${API}/v1/demo-tape/start`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: cookieHeader,
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: JSON.stringify({ tape_id: TAPE, speed: SPEED, max_gap_ms: GAP }),
    });
    if (!startRes.ok) throw new Error(await startRes.text());
    const { conversation_id: cid } = await startRes.json();
    await page.goto(new URL(`index.webapp.html#/conversations/${cid}`, base).href, {
      waitUntil: "load",
      timeout: 30000,
    });
    await page.getByRole("button", { name: /授权开赛/ }).first().waitFor({ state: "visible", timeout: 60000 });
    await page.getByRole("button", { name: /授权开赛/ }).first().click();
    console.log("authorized", cid);

    for (let i = 0; i < 60; i++) {
      const open = page.getByRole("button", { name: /打开辩论室/ });
      if (await open.first().isVisible().catch(() => false)) {
        await open.first().click();
        break;
      }
      if (await page.getByRole("button", { name: /^辩论室$/ }).isVisible().catch(() => false)) break;
      await page.waitForTimeout(500);
    }
    await page.getByRole("button", { name: /^辩论室$/ }).click().catch(() => {});

    // Wait until round-4 chapter chip exists (debate settled enough; tape is 4 rounds)
    for (let i = 0; i < 180; i++) {
      const r4 = page.getByRole("button", { name: "第4轮", exact: true });
      if (await r4.isVisible().catch(() => false)) {
        console.log("r4 chip visible at", i);
        break;
      }
      if (i % 15 === 0) console.log("waiting r4 chip", i);
      await page.waitForTimeout(1500);
    }
    await page.waitForTimeout(2000);

    for (const job of jobs) {
      const chipLabel = `第${job.round}轮`;
      console.log("→", job.id, chipLabel);
      const chip = page.getByRole("button", { name: chipLabel, exact: true });
      if (!(await chip.isVisible().catch(() => false))) {
        result.miss.push({ id: job.id, reason: `chip ${chipLabel} missing` });
        continue;
      }
      await chip.click();
      await page.waitForTimeout(800);

      // Anchor id is on SectionHeader only; round body is the parent <div key=round>.
      const anchored = await page.evaluate((round) => {
        const header = document.getElementById(`debate-round-${round}`);
        const section = header?.parentElement;
        if (!section) return false;
        section.scrollIntoView({ block: "start" });
        return true;
      }, job.round);
      if (!anchored) {
        result.miss.push({ id: job.id, reason: `missing #debate-round-${job.round}` });
        console.log("MISS", job.id, "no anchor");
        continue;
      }
      await page.waitForTimeout(500);

      await page.evaluate((round) => {
        const section = document.getElementById(`debate-round-${round}`)?.parentElement;
        if (!section) return;
        section.querySelectorAll("button").forEach((b) => {
          if (/展开全文/.test(b.textContent || "")) b.click();
        });
      }, job.round);
      await page.waitForTimeout(500);

      const blobInfo = await page.evaluate((job) => {
        const section = document.getElementById(`debate-round-${job.round}`)?.parentElement;
        const blob = (section?.innerText || "").replace(/\s+/g, " ");
        const hit = job.anyOf
          ? job.needles.some((n) => blob.includes(n))
          : job.needles.every((n) => blob.includes(n));
        return { hit, len: blob.length, snip: blob.slice(0, 160) };
      }, job);
      console.log("section", job.id, blobInfo);

      // Pin round section at top — do NOT wheel further (avoids spilling into 结辩).
      await page.evaluate((round) => {
        document.getElementById(`debate-round-${round}`)?.parentElement?.scrollIntoView({
          block: "start",
        });
      }, job.round);
      await page.waitForTimeout(400);

      // Content gate: never overwrite a still with a miss viewport.
      if (!blobInfo.hit) {
        result.miss.push({
          id: job.id,
          reason: `needles not in round parent (len=${blobInfo.len}); left disk untouched`,
          snip: blobInfo.snip,
        });
        console.log("MISS (no write)", job.id);
        continue;
      }
      const path = resolve(stillsDir, `${job.id}.png`);
      await page.screenshot({ path, fullPage: false });
      result.ok.push(job.id);
      console.log("OK", job.id);
    }
  } catch (e) {
    result.fatal = String(e?.stack ?? e);
    console.error(result.fatal);
  } finally {
    await browser.close();
    await server.close();
  }
  console.log("ROUNDS", JSON.stringify(result));
  process.exitCode = result.fatal || result.miss.length ? 1 : 0;
}

/**
 * @param {{ tape?: string, out?: string, only?: string }} opts
 */
export async function run(opts = {}) {
  const paths = resolveCapturePaths(opts);
  stillsDir = paths.stillsDir;
  TAPE = paths.tape;
  API = (process.env.PROMO_API ?? "http://localhost:8015").replace(/\/$/, "");
  PORT = Number(process.env.PROMO_PORT ?? 5174);
  SPEED = Number(process.env.PROMO_SPEED ?? 25);
  GAP = Number(process.env.PROMO_GAP ?? 300);
  process.env.VITE_API_URL = API;
  await main(opts.only);
}
