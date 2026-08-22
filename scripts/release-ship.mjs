#!/usr/bin/env node
/**
 * 半自动发版清单——打印步骤与验收探针，不替你点 Publish / 不自动部署。
 *
 *   pnpm release:ship                 # 全端 full（默认）
 *   pnpm release:ship -- --track api  # 仅后端热修轨
 *   pnpm release:ship -- --sha abc1234
 *   pnpm release:ship -- --check      # 额外探测：git / 桌面·Android draft 资产（需 gh）/ updater feed
 *
 * 公告两段式（定案 D · 工作流 A）：
 *   预告 = 人定「今天发 + 约时」后立刻 → pnpm release:notice -- --phase preview …
 *         （与 release:gate / 修拦阻并行；红了改约时或归档预告；部署前仍须 gate 全绿）
 *   收口 = 桌面转正 + 官网（或 api-only 验收）后 → --phase done
 *
 * 权威命令细节 → .cursor/rules/cursor-deploy.mdc · docs/05 发布与门禁 / 产品公告文案模板
 */
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  cdnUrl,
  desktopChannelPrefix,
  desktopLegacyFlatPrefix,
} from "../apps/website/functions/_lib/downloadsCdn.mjs";
import {
  closeConnections,
  formatCertLine,
  formatDnsLine,
  pinnedRequest,
} from "../deploy/scripts/public-dns-https.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function arg(name, fallback = "") {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1 || i + 1 >= process.argv.length) return fallback;
  return process.argv[i + 1];
}

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

function sh(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, {
    cwd: ROOT,
    encoding: "utf8",
    shell: process.platform === "win32",
    ...opts,
  });
  return {
    status: r.status ?? 1,
    out: `${r.stdout ?? ""}${r.stderr ?? ""}`.trim(),
  };
}

function readVersions() {
  const api =
    readFileSync(join(ROOT, "apps/server/pyproject.toml"), "utf8").match(
      /^version\s*=\s*"([^"]+)"/m,
    )?.[1] ?? "?";
  const desktop = JSON.parse(
    readFileSync(join(ROOT, "apps/desktop/package.json"), "utf8"),
  ).version;
  const mobile = JSON.parse(
    readFileSync(join(ROOT, "apps/mobile/package.json"), "utf8"),
  ).version;
  const admin = JSON.parse(
    readFileSync(join(ROOT, "apps/admin/package.json"), "utf8"),
  ).version;
  return { api, desktop, mobile, admin };
}

function headSha() {
  const r = sh("git", ["rev-parse", "--short=8", "HEAD"]);
  return r.status === 0 ? r.out.split(/\r?\n/)[0] : "UNKNOWN";
}

function printStep(n, title, lines) {
  console.log(`\n${n}. ${title}`);
  for (const line of lines) console.log(`   ${line}`);
}

function checkDesktopDraft(desktopVer) {
  const tag = `v${desktopVer}`;
  const r = sh("gh", [
    "release",
    "view",
    tag,
    "--repo",
    "Lawofall/AgentCore-releases",
    "--json",
    "isDraft,assets",
  ]);
  if (r.status !== 0) {
    console.log(`   · draft ${tag}: 未找到或 gh 不可用`);
    return;
  }
  try {
    const j = JSON.parse(r.out);
    const names = (j.assets ?? []).map((a) => a.name);
    const need = [
      `AgentCore-${desktopVer}-win-x64.exe`,
      "latest.yml",
      `AgentCore-${desktopVer}-mac-arm64.dmg`,
      "latest-mac.yml",
    ];
    const missing = need.filter((n) => !names.includes(n));
    console.log(
      `   · ${tag} draft=${j.isDraft} assets=${names.length}${missing.length ? ` 缺: ${missing.join(", ")}` : " （Win+Mac 线索齐）"}`,
    );
    if (!j.isDraft) {
      console.log("   · 已非 draft → 可 deploy:pages");
    } else if (!missing.length) {
      console.log(
        `   · 可转正: gh release edit ${tag} --repo Lawofall/AgentCore-releases --draft=false --latest`,
      );
    }
  } catch {
    console.log("   · 无法解析 gh JSON");
  }
}

function checkAndroidDraft(mobileVer) {
  const tag = `android-v${mobileVer}`;
  const r = sh("gh", [
    "release",
    "view",
    tag,
    "--repo",
    "Lawofall/AgentCore-releases",
    "--json",
    "isDraft,assets",
  ]);
  if (r.status !== 0) {
    console.log(`   · draft ${tag}: 未找到或 gh 不可用`);
    return;
  }
  try {
    const j = JSON.parse(r.out);
    const names = (j.assets ?? []).map((a) => a.name);
    const need = [`AgentCore-${mobileVer}-android.apk`];
    const missing = need.filter((n) => !names.includes(n));
    console.log(
      `   · ${tag} draft=${j.isDraft} assets=${names.length}${missing.length ? ` 缺: ${missing.join(", ")}` : " （APK 齐）"}`,
    );
    if (!j.isDraft) {
      console.log("   · 已非 draft → 官网 /download 可露 Android 链");
    } else if (!missing.length) {
      console.log(
        `   · 可转正: gh release edit ${tag} --repo Lawofall/AgentCore-releases --draft=false`,
      );
    }
  } catch {
    console.log("   · 无法解析 gh JSON");
  }
}

/**
 * 轻量 updater feed 健康检查：只看可达性 / TLS 到期 / feed 里的 version。
 *
 * 刻意不测速（现有 probe-updater-cdn 才拉 Range，近 9MB），也刻意**不断言**
 * feed version == 待发版本——`--check` 在部署前跑，feed 本就还是上一版。
 * 解析走公开 DoH（本机 resolver 会指到 Tunnel 源站 IP，报假的证书过期）。
 */
async function checkUpdaterFeed() {
  const feeds = [
    {
      label: "desktop/stable",
      url: `${cdnUrl(desktopChannelPrefix("stable"))}/latest.yml`,
    },
    {
      label: "desktop/（扁平·旧客户端）",
      url: `${cdnUrl(desktopLegacyFlatPrefix())}/latest.yml`,
    },
  ];
  let shownHost = false;
  /** @type {string[]} */
  const versions = [];
  for (const feed of feeds) {
    try {
      const res = await pinnedRequest(feed.url, {
        idleTimeoutMs: 15_000,
        deadlineMs: 15_000,
      });
      const { text } = await res.text();
      if (!shownHost) {
        shownHost = true;
        console.log(`   · ${formatDnsLine(res.dns)}`);
        console.log(`   · ${formatCertLine(res.tls)} · 连到 ${res.ip}`);
        if (res.tls && res.tls.daysLeft <= 30) {
          console.log(
            "   · ⚠ 下载域证书临近到期 → 确认 Cloudflare 边缘证书自动续期未失效",
          );
        }
      }
      const version = text.match(/^version:\s*['"]?([^\s'"]+)/m)?.[1] ?? "?";
      versions.push(version);
      const ok = res.status === 200;
      console.log(
        `   · ${feed.label}/latest.yml: HTTP ${res.status}${ok ? "" : " ✗"}` +
          ` version=${version} ttfb=${Math.round(res.ttfbMs)}ms cf=${res.header("cf-cache-status") ?? "-"}`,
      );
    } catch (err) {
      console.log(
        `   · ${feed.label}/latest.yml: 探测失败 — ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }
  if (versions.length === 2 && versions[0] !== versions[1]) {
    console.log(
      `   · ⚠ stable 与扁平镜像版本不一致（${versions[0]} vs ${versions[1]}）→ 老客户端可能停更，检查 sync:release-cdn`,
    );
  }
  console.log(
    "   · 上面是 feed 当前实际值；部署前它仍是上一版属正常，本探针不做版本断言",
  );
}

async function main() {
  const track = (arg("track", "full").trim() || "full").toLowerCase();
  if (track !== "full" && track !== "api") {
    console.error("ERROR: --track must be full|api");
    process.exit(1);
  }
  const shaArg = arg("sha").trim();
  const sha = shaArg || headSha();
  const doCheck = hasFlag("check");
  const v = readVersions();
  const win = process.platform === "win32";

  console.log(`\n══ release:ship · track=${track} · sha=${sha} ══`);
  console.log(
    `工作树版本 api ${v.api} / desktop ${v.desktop} / mobile ${v.mobile} / admin ${v.admin}`,
  );
  console.log(
    "本脚本只打印清单与探针；不自动 bump / push / deploy / Publish。\n",
  );

  let n = 1;
  printStep(n++, "【公告·预告】人定约时后立刻发（工作流 A · 与门禁并行）", [
    track === "full"
      ? `pnpm release:notice -- --phase preview --kind release --at HH:MM --highlights "亮点1；亮点2；亮点3；亮点4；亮点5"`
      : `pnpm release:notice -- --phase preview --kind hotfix --at HH:MM --summary "一句话变更"`,
    "勿等 gate 全绿才预告；门禁不过 → 改约时或 Admin 归档预告",
    "dry-run 可先: 同上命令加 --dry-run",
  ]);

  printStep(n++, "本地门禁（部署前必须全量非 lite 全绿）", [
    win
      ? "Win 默认串行（避免 contracts∥desktop 写盘撞锁）：pnpm release:gate"
      : "pnpm release:gate",
    "日常迭代可用 pnpm release:gate:lite（不可替代全量）",
  ]);

  printStep(n++, "版本 bump（按轨）", [
    track === "full"
      ? "pnpm bump-version api patch && pnpm bump-version desktop patch && pnpm bump-version mobile patch"
      : "pnpm bump-version api patch",
    "bump api 会连带同步 uv.lock 与 openapi info.version（两者漏改都会撞 CI 漂移门禁）",
  ]);

  printStep(n++, "提交 + push", [
    `git add -A && git commit  # 信息示例: release: api ${v.api} / desktop ${v.desktop} / …`,
    "git push origin HEAD",
  ]);

  printStep(n++, "等云端 CI 全绿（本地门禁跑 Windows，抓不到 Linux 面）", [
    "gh run list --workflow CI --branch master --limit 1",
    "gh run watch <id> --exit-status   # 红灯或未跑完不得上后端",
  ]);

  printStep(n++, "后端上线（必须先于客户端）", [
    `pnpm deploy:backend ${sha}`,
    "验收: GET /readyz · GET /version → git_sha 对齐（勿在 chat 回显主机）",
  ]);

  if (track === "full") {
    printStep(n++, "桌面 Win + Mac（可并行）", [
      "pnpm -C apps/desktop release:win",
      'gh workflow run "Release Desktop" --repo Lawofall/AgentCore -f platform=mac',
      `齐资产后转正: gh release edit v${v.desktop} --repo Lawofall/AgentCore-releases --draft=false --latest`,
    ]);

    printStep(n++, "Android APK（与桌面 tag 分轨 android-v*）", [
      "pnpm -C apps/mobile release:android   # 打签 APK → draft upload；CDN 由脚本末尾 sync；出包前自动跑公网 Capacitor CORS 预检",
      "真机冒烟（签名安装 / 系统 WebView 渲染 / 端到端 SSE）",
      `冒烟绿后转正: gh release edit android-v${v.mobile} --repo Lawofall/AgentCore-releases --draft=false`,
      "本清单不自动跑 release:android；漏 Publish 则官网/CDN 停在旧版",
    ]);

    printStep(n++, "客户端静态面（后端已上后即可；官网等桌面 Publish）", [
      "pnpm -C apps/admin deploy:production",
      "pnpm -C apps/desktop deploy:web",
      "pnpm -C apps/website deploy:pages   # 须在桌面 release 已 Publish 之后；Android 链亦须 android-v* 已转正",
    ]);
  } else {
    printStep(n++, "客户端", [
      "纯后端热修通常不必跟发桌面/官网；协议变更则同日跟 admin（见发布与门禁 §7.4）",
    ]);
  }

  printStep(n++, "【公告·收口】验收通过后", [
    track === "full"
      ? `pnpm release:notice -- --phase done --kind release --versions "api ${v.api} / 桌面 ${v.desktop} / 手机 ${v.mobile}"`
      : `pnpm release:notice -- --phase done --kind hotfix --summary "一句话变更"`,
    "可选: Admin 归档预告条，避免双横幅叠放",
  ]);

  printStep(n++, "发布点 tag（仅标记，不触发部署）", [
    `git tag prod-${sha} ${sha} && git push origin prod-${sha}`,
    track === "full"
      ? `git tag desktop-v${v.desktop} ${sha} && git push origin desktop-v${v.desktop}`
      : "(api-only 可只打 prod-*)",
  ]);

  if (doCheck) {
    console.log("\n── --check 探针 ──");
    const st = sh("git", ["status", "-sb"]);
    console.log(`   · git: ${st.out.split(/\r?\n/)[0] ?? "?"}`);
    if (track === "full") {
      checkDesktopDraft(v.desktop);
      checkAndroidDraft(v.mobile);
    }
    try {
      await checkUpdaterFeed();
    } finally {
      closeConnections();
    }
  }

  console.log(
    "\n✓ 清单打印完毕。关键节点（Publish / 公告）须人确认后再执行。\n",
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
