#!/usr/bin/env node
/**
 * 发版/热修产品公告（两段式）——套模板后调用 publish:notice。
 *
 *   pnpm release:notice -- --phase preview --kind release --at 20:00 \
 *     --highlights "亮点1；亮点2；亮点3；亮点4；亮点5"
 *   pnpm release:notice -- --phase done --kind release --versions "api 0.3.39 / 桌面 0.6.39"
 *   pnpm release:notice -- --phase preview --kind hotfix --at 14:30 --summary "修复登录超时"
 *   pnpm release:notice -- --dry-run --phase done --kind hotfix
 *
 * 默认只发收口（--phase done）。预告（--phase preview）仅例外：破坏性迁移 / 无法空闲切流。
 * 准备/切流工作流 → docs/05-平台与运维/发布与门禁.md · release:ship
 * 文案权威 → docs/05-平台与运维/产品公告文案模板.md
 * Win：标题/正文走临时文件（--title-file / --body-file），避免 shell:true 拆碎空格标题。
 * 维护/政策/故障仍走 Admin UI，不进本脚本。
 * 默认 CTA：检查更新 → /more/about（可用 --no-cta 关闭，或 --cta-label/--cta-url 覆盖）。
 * 文案权威 → docs/05-平台与运维/产品公告文案模板.md
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(import.meta.url), "..", "..");

/** 与 Admin release/hotfix 模板对齐：应用内关于页可检查更新。 */
const DEFAULT_CTA = {
  label: "检查更新",
  url: "/more/about",
};

function arg(name, fallback = "") {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1 || i + 1 >= process.argv.length) return fallback;
  return process.argv[i + 1];
}

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

function printHelp() {
  console.log(`Usage: pnpm release:notice -- --phase preview|done --kind release|hotfix [options]

Options:
  --at HH:MM              预告约时（preview 必填，北京时间）
  --highlights "a；b；c"   全端发版亮点（≤5 条，可用；或换行分隔）
  --summary "…"           热修一句话摘要
  --versions "api x / 桌面 y / 手机 z"  收口正文版本行（可选）
  --end-hours N|none      传给 publish:notice（preview 默认 4；done 默认 2）
  --dry-run               只打印标题/正文，不发布
  --surface both|banner|inbox|modal   默认 both
  --severity high|normal|critical     默认 high
  --cta-label "…"         覆盖默认 CTA 文案（检查更新）
  --cta-url "…"           覆盖默认 CTA（/more/about；可 https 或应用内路径）
  --no-cta                不带 CTA（无动作场景）
`);
}

function splitHighlights(raw) {
  return raw
    .split(/[；;\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 5);
}

function buildReleasePreview({ at, highlights }) {
  const lines = splitHighlights(highlights);
  if (!lines.length) {
    throw new Error("release preview 需要 --highlights（≤5 条用户可感知变化）");
  }
  const title = `约 ${at} 发版 · 请按需规划好时间 · 提前停止使用 AI 功能`;
  const body = `新版本将于今天约 ${at} 起陆续上线。

更新期间 AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能，以免进行中的对话或任务被中断。

升级方式：
· 桌面：应用内检查更新，或到官网重新下载安装
· 手机 / Web：刷新页面，或按官网指引安装新包

本次亮点：
${lines.map((l) => `· ${l}`).join("\n")}

完成后按上面方式升级即可继续使用。`;
  return { title, body };
}

function buildReleaseDone({ versions }) {
  const ver = versions.trim() || "新版本";
  const title = `发版已完成 · 请按需更新客户端`;
  const body = `${ver} 已陆续上线。

若刚才按预告暂停了使用：桌面请检查更新或到官网下载；手机 / Web 刷新即可。

预告横幅可忽略或待过期；详情仍可在消息页「AgentCore 官方」查看。`;
  return { title, body };
}

function buildHotfixPreview({ at, summary }) {
  if (!summary.trim()) {
    throw new Error("hotfix preview 需要 --summary（一句话用户可感知变更）");
  }
  const title = `约 ${at} 更新 · 请按需规划好时间 · 提前停止使用 AI 功能`;
  const body = `我们将于今天约 ${at} 进行一次系统更新，预计 1–3 分钟。

更新期间 AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能，以免进行中的对话或任务被中断。

更新完成后刷新即可；一般无需重装客户端。
本次：${summary.trim()}

若结束后仍异常，打开消息页「AgentCore 官方」或稍后重试。`;
  return { title, body };
}

function buildHotfixDone({ summary }) {
  const bit = summary.trim() ? `（${summary.trim()}）` : "";
  const title = `更新已完成 · 可继续使用`;
  const body = `系统短更新已完成${bit}。

请刷新页面或重开客户端后继续；一般无需重装。
若仍异常，打开消息页「AgentCore 官方」或稍后重试。`;
  return { title, body };
}

function resolveCta() {
  if (hasFlag("no-cta")) return { label: "", url: "" };
  const label = arg("cta-label", DEFAULT_CTA.label).trim();
  const url = arg("cta-url", DEFAULT_CTA.url).trim();
  if ((label && !url) || (!label && url)) {
    throw new Error("--cta-label 与 --cta-url 须成对填写（或用 --no-cta）");
  }
  return { label, url };
}

function main() {
  if (hasFlag("help") || hasFlag("h")) {
    printHelp();
    process.exit(0);
  }

  const phase = arg("phase").trim();
  const kind = arg("kind").trim();
  const at = arg("at").trim();
  const highlights = arg("highlights");
  const summary = arg("summary");
  const versions = arg("versions");
  const dryRun = hasFlag("dry-run");
  const surface = arg("surface", "both").trim() || "both";
  const severity = arg("severity", "high").trim() || "high";

  if (phase !== "preview" && phase !== "done") {
    printHelp();
    console.error("ERROR: --phase must be preview|done");
    process.exit(1);
  }
  if (kind !== "release" && kind !== "hotfix") {
    printHelp();
    console.error("ERROR: --kind must be release|hotfix");
    process.exit(1);
  }
  if (phase === "preview" && !/^\d{1,2}:\d{2}$/.test(at)) {
    console.error("ERROR: preview 需要 --at HH:MM（如 20:00）");
    process.exit(1);
  }

  let cta;
  try {
    cta = resolveCta();
  } catch (err) {
    console.error(`ERROR: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  }

  let built;
  try {
    if (kind === "release" && phase === "preview") {
      built = buildReleasePreview({ at, highlights });
    } else if (kind === "release" && phase === "done") {
      built = buildReleaseDone({ versions });
    } else if (kind === "hotfix" && phase === "preview") {
      built = buildHotfixPreview({ at, summary });
    } else {
      built = buildHotfixDone({ summary });
    }
  } catch (err) {
    console.error(`ERROR: ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  }

  const defaultEnd =
    phase === "preview" ? "4" : kind === "hotfix" ? "2" : "2";
  const endHours = arg("end-hours", defaultEnd).trim() || defaultEnd;

  console.log(`\n── release:notice ${kind}/${phase} ──`);
  console.log(`title: ${built.title}`);
  console.log(
    `surface=${surface} severity=${severity} end-hours=${endHours} card_template=service`,
  );
  if (cta.label && cta.url) {
    console.log(`cta: ${cta.label} → ${cta.url}`);
  } else {
    console.log("cta: (none)");
  }
  console.log("--- body ---");
  console.log(built.body);
  console.log("------------\n");

  if (dryRun) {
    console.log("dry-run：未调用 publish:notice");
    return;
  }

  const dir = mkdtempSync(join(tmpdir(), "agentcore-notice-"));
  const bodyFile = join(dir, "body.txt");
  const titleFile = join(dir, "title.txt");
  writeFileSync(bodyFile, `${built.body}\n`, "utf8");
  writeFileSync(titleFile, `${built.title}\n`, "utf8");

  const args = [
    "publish:notice",
    "--",
    "--title-file",
    titleFile,
    "--body-file",
    bodyFile,
    "--severity",
    severity,
    "--surface",
    surface,
    "--dismiss",
    "once",
    "--end-hours",
    endHours,
    "--card-template",
    "service",
  ];
  if (cta.label && cta.url) {
    args.push("--cta-label", cta.label, "--cta-url", cta.url);
  }
  const result = spawnSync("pnpm", args, {
    cwd: ROOT,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

main();
