#!/usr/bin/env node
/**
 * Local release gate — isomorphic with `.github/workflows/ci.yml`, including
 * desktop typecheck + conformance (CI frontend job already runs both).
 *
 *   pnpm release:gate                    # full run（发布验证必须全量）
 *   pnpm release:gate:lite               # 日常迭代：跳过 desktop shoot + smoke
 *   pnpm release:gate --lite             # 同上（亦认 RELEASE_GATE_LITE=1）
 *   RELEASE_GATE_SKIP_SHOOT=1            # 仅跳过 shoot，仍跑 smoke:webapp:ci
 *   pnpm release:gate --from desktop     # 断点续跑：从 desktop 段开始
 *   pnpm release:gate --only backend     # 只跑单段（修复迭代用）
 *
 * Sections (in order): backend, contracts, desktop, mobile, admin.
 * When both contracts and desktop are enabled, they run in parallel child
 * processes (CI already splits them into separate jobs). Set
 * RELEASE_GATE_SERIAL=1 to force the old sequential order.
 * `--from`/`--only`/`--lite` are local iteration aids — a release still requires
 * one uninterrupted **full** (non-lite) pass.
 * On Windows, RELEASE_GATE_SERIAL defaults to 1 (avoid gen-types write races);
 * set RELEASE_GATE_SERIAL=0 to opt into contracts∥desktop parallel.
 *
 * Lite skips the desktop screenshot matrix (~4min with 4 workers) + webapp smoke (port-fragile);
 * lint / typecheck / vitest / conformance stay. Full gate still runs shoot with
 * SHOOT_FRAMES=3 and smoke:webapp:ci. Before smoke, freeListenPorts clears
 * leftover AgentCore vite on SMOKE_PORT (default 5175) to reduce port flakes.
 *
 * Any non-zero step fails the whole gate. Backend uses unit pytest
 * (`--ignore=tests/integration`) for local runnability; CI still runs full
 * pytest with Postgres. Because that exclusion makes a green gate narrower than it
 * reads, a successful run ends with an explicit "not covered" block naming the
 * contracts that only the integration suite pins (printIntegrationCoverageGap).
 *
 * Contract drift (local): snapshot the artifacts BEFORE regenerating, then fail if
 * the regen rewrote any of them (stale = a source change shipped without its
 * generated artifacts) or if a second regen differs (non-idempotent). On a clean
 * tree, disk == HEAD, so "regen rewrote nothing" is exactly CI's `git diff
 * --exit-code`; on a WIP tree it asks the same question against the tree's own
 * sources instead of misreading intentional uncommitted regens as drift. Those
 * uncommitted-but-in-sync artifacts still print as a note — they must ride the
 * release commit, and CI re-checks against the pushed HEAD.
 */
import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import {
  createReadStream,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GATE_SCRIPT = fileURLToPath(import.meta.url);
const ROOT = join(dirname(GATE_SCRIPT), "..");
const SERVER = join(ROOT, "apps", "server");

// Every artifact `regenContracts()` writes. A generated file missing from this list is
// invisible to the drift gate — the same hole EVAL-A6 named, one file smaller.
const CONTRACT_DRIFT_PATHS = [
  "apps/server/openapi.json",
  "packages/contract-rest-types/src/api.generated.ts",
  "packages/contract-rest-types/src/paths.generated.ts",
  "packages/contract-types/src/eventTypes.generated.ts",
  "packages/contract-types/src/events.generated.ts",
  "packages/contract-types/src/interactionKinds.generated.ts",
  "packages/contract-types/src/errorCodes.generated.ts",
  "packages/protocol-conformance/fixtures",
];

function run(label, cmd, args, opts = {}) {
  console.log(`\n→ ${label}`);
  const result = spawnSync(cmd, args, {
    cwd: opts.cwd ?? ROOT,
    stdio: "inherit",
    env: { ...process.env, ...opts.env },
    shell: process.platform === "win32",
  });
  if (result.status !== 0) {
    console.error(`\n✗ release:gate FAILED — ${label}`);
    process.exit(result.status ?? 1);
  }
}

/** Run a long noisy command; keep full log on disk, only stream a short tail on failure.
 *
 * Inheriting megabytes of sim/LLM info logs into the agent terminal is slow enough on
 * Windows to trip pytest-timeout (60s) even when the test itself is fine.
 */
function runLogged(label, cmd, args, opts = {}) {
  console.log(`\n→ ${label}`);
  const logDir = join(tmpdir(), "agentcore-release-gate");
  mkdirSync(logDir, { recursive: true });
  const logPath = join(logDir, `${label.replace(/[^\w.-]+/g, "_")}.log`);
  const result = spawnSync(cmd, args, {
    cwd: opts.cwd ?? ROOT,
    encoding: "utf8",
    env: { ...process.env, ...opts.env },
    shell: process.platform === "win32",
    maxBuffer: 64 * 1024 * 1024,
  });
  const out = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  writeFileSync(logPath, out, "utf8");
  // Progress crumbs for the live terminal (last non-empty lines of pytest -q).
  const crumbs = out
    .split(/\r?\n/)
    .map((l) => l.trimEnd())
    .filter((l) => l && !l.includes("llm.request") && !l.includes("llm.response"));
  for (const line of crumbs.slice(-8)) console.log(line);
  console.log(`  (full log: ${logPath})`);
  if (result.status !== 0) {
    console.error(`\n✗ release:gate FAILED — ${label}`);
    console.error("--- log tail ---");
    console.error(crumbs.slice(-40).join("\n"));
    process.exit(result.status ?? 1);
  }
}

function section(title) {
  console.log(`\n══ ${title} ══`);
}

function listFiles(relPath) {
  const abs = join(ROOT, relPath);
  if (!existsSync(abs)) return [];
  const st = statSync(abs);
  if (st.isFile()) return [abs];
  const out = [];
  for (const name of readdirSync(abs)) {
    const child = join(abs, name);
    if (statSync(child).isDirectory()) {
      out.push(...listFiles(join(relPath, name)));
    } else {
      out.push(child);
    }
  }
  return out;
}

function hashFile(absPath) {
  return new Promise((resolve, reject) => {
    const h = createHash("sha256");
    const s = createReadStream(absPath);
    s.on("data", (chunk) => h.update(chunk));
    s.on("error", reject);
    s.on("end", () => resolve(h.digest("hex")));
  });
}

/** sha256 of every committed contract artifact, keyed by absolute path. */
async function snapshotContracts() {
  const files = CONTRACT_DRIFT_PATHS.flatMap(listFiles).sort();
  const snap = new Map();
  for (const f of files) snap.set(f, await hashFile(f));
  return snap;
}

/** Files a regen added / removed / rewrote, relative to ROOT. */
function snapshotDelta(before, after) {
  const rel = (abs) => abs.slice(ROOT.length + 1).replaceAll("\\", "/");
  const changed = [];
  for (const [path, hash] of after) {
    if (!before.has(path)) changed.push(`+ ${rel(path)}`);
    else if (before.get(path) !== hash) changed.push(`M ${rel(path)}`);
  }
  for (const path of before.keys()) {
    if (!after.has(path)) changed.push(`- ${rel(path)}`);
  }
  return changed.sort();
}

function snapshotToJson(snap) {
  return JSON.stringify(Object.fromEntries(snap));
}

function snapshotFromJson(text) {
  return new Map(Object.entries(JSON.parse(text)));
}

function regenContracts() {
  run("gen-types", "node", ["scripts/gen-types.mjs"]);
  run("conformance export", "uv", ["run", "python", "-m", "agentcore.conformance.export"], {
    cwd: SERVER,
  });
}

/** Baseline = artifacts as they sit on disk BEFORE this run regenerates anything.
 *
 * Must be captured before the first `regenContracts()` — including the parent's
 * warm-up regen on the parallel path, which passes its own baseline down to the
 * contracts child via RELEASE_GATE_CONTRACT_BASELINE.
 */
async function captureContractBaseline() {
  const handoff = process.env.RELEASE_GATE_CONTRACT_BASELINE;
  if (handoff && existsSync(handoff)) return snapshotFromJson(readFileSync(handoff, "utf8"));
  return snapshotContracts();
}

function writeContractBaseline(snap) {
  const dir = join(tmpdir(), "agentcore-release-gate");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `contract-baseline-${process.pid}.json`);
  writeFileSync(path, snapshotToJson(snap), "utf8");
  return path;
}

/** Contract drift gate — the local half of CI's `git diff --exit-code`.
 *
 * Two distinct failures, both hard:
 *  1. STALE — regen rewrote an artifact, i.e. what is on disk does not match what
 *     the sources produce. On a clean tree (disk == HEAD) this is exactly CI's
 *     "fail on uncommitted drift"; on a WIP tree it asks the honest local question
 *     ("are the artifacts in sync with THIS tree's sources?") instead of flagging
 *     every intentional uncommitted regen.
 *  2. NON-IDEMPOTENT — a second regen differs from the first, so no commit can ever
 *     satisfy CI.
 *
 * Artifacts that are in sync but still uncommitted are a note, not a failure: the
 * gate runs before the release commit, and CI re-checks against the pushed HEAD.
 */
async function assertContractsInSync(baseline) {
  console.log("\n→ contract drift (artifacts ↔ sources)");
  const afterFirst = await snapshotContracts();
  const stale = snapshotDelta(baseline, afterFirst);
  if (stale.length) {
    console.error("\n✗ release:gate FAILED — committed contract artifacts were stale");
    console.error(`  Regen rewrote ${stale.length} file(s); the tree shipped a source change`);
    console.error("  without its generated artifacts (CI's `git diff --exit-code` would fail):");
    for (const line of stale.slice(0, 40)) console.error(`    ${line}`);
    if (stale.length > 40) console.error(`    …(+${stale.length - 40} more)`);
    console.error("  The regen above already fixed the tree — review and commit those files.");
    process.exit(1);
  }
  console.log(`  in sync — regen rewrote nothing (${afterFirst.size} artifacts)`);

  console.log("\n→ contract regen idempotence");
  regenContracts();
  const afterSecond = await snapshotContracts();
  const unstable = snapshotDelta(afterFirst, afterSecond);
  if (unstable.length) {
    console.error("\n✗ release:gate FAILED — contract regen not idempotent");
    console.error("  First and second regen produced different artifacts:");
    for (const line of unstable.slice(0, 40)) console.error(`    ${line}`);
    process.exit(1);
  }
  console.log("  stable across two regens");

  const porcelain = spawnSync(
    "git",
    ["status", "--porcelain", "--", ...CONTRACT_DRIFT_PATHS],
    { cwd: ROOT, encoding: "utf8", shell: process.platform === "win32" },
  );
  const dirty = (porcelain.stdout || "").trim();
  if (dirty) {
    console.log(
      "  note: in sync with this tree's sources but not yet committed — these must ride the",
    );
    console.log("  release commit, or CI's drift gate goes red on the pushed HEAD:");
    console.log(
      dirty
        .split("\n")
        .map((l) => `    ${l.trim()}`)
        .join("\n"),
    );
  } else {
    console.log("  contract artifacts match HEAD");
  }
}

const SECTION_ORDER = ["backend", "contracts", "desktop", "mobile", "admin"];

function parseSectionArgs(argv) {
  let from = null;
  let only = null;
  let lite = process.env.RELEASE_GATE_LITE === "1";
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === "--from" && argv[i + 1]) from = argv[++i];
    else if (argv[i] === "--only" && argv[i + 1]) only = argv[++i];
    else if (argv[i] === "--lite") lite = true;
  }
  for (const [flag, value] of [
    ["--from", from],
    ["--only", only],
  ]) {
    if (value && !SECTION_ORDER.includes(value)) {
      console.error(`${flag} ${value}: unknown section (${SECTION_ORDER.join(", ")})`);
      process.exit(2);
    }
  }
  if (from && only) {
    console.error("--from and --only are mutually exclusive");
    process.exit(2);
  }
  return { from, only, lite };
}

function sectionEnabled(name, { from, only }) {
  if (only) return name === only;
  if (from) return SECTION_ORDER.indexOf(name) >= SECTION_ORDER.indexOf(from);
  return true;
}

/** Spawn a nested `release:gate --only <section>` so contracts ∥ desktop can
 *  overlap wall-clock without blocking the parent on spawnSync. */
function runSectionChild(only, { lite = false, contractBaselinePath = null } = {}) {
  return new Promise((resolve, reject) => {
    const args = [GATE_SCRIPT, "--only", only];
    if (lite) args.push("--lite");
    console.log(`\n↗ parallel child: --only ${only}${lite ? " --lite" : ""}`);
    const child = spawn(process.execPath, args, {
      cwd: ROOT,
      stdio: "inherit",
      env: {
        ...process.env,
        RELEASE_GATE_SERIAL: "1",
        ...(lite ? { RELEASE_GATE_LITE: "1" } : {}),
        ...(contractBaselinePath
          ? { RELEASE_GATE_CONTRACT_BASELINE: contractBaselinePath }
          : {}),
      },
      shell: false,
    });
    child.on("error", reject);
    child.on("exit", (code, signal) => {
      if (code === 0) resolve();
      else {
        reject(
          new Error(
            `release:gate --only ${only} failed (code=${code}, signal=${signal})`,
          ),
        );
      }
    });
  });
}

async function runContractsSection(baseline) {
  section("contracts");
  regenContracts();
  run("legal md check", "pnpm", ["sync:legal:check"]);
  run("doc section pointers", "node", [
    "scripts/check-doc-section-pointers.mjs",
  ]);
  await assertContractsInSync(baseline);
}

/** Spawn a gate step without blocking siblings; used for independent desktop checks. */
function runAsync(label, cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    console.log(`\n→ ${label}`);
    const child = spawn(cmd, args, {
      cwd: opts.cwd ?? ROOT,
      stdio: "inherit",
      env: { ...process.env, ...opts.env },
      shell: process.platform === "win32",
    });
    child.on("error", reject);
    child.on("exit", (code, signal) => {
      if (code === 0) resolve();
      else {
        reject(
          new Error(`release:gate FAILED — ${label} (code=${code}, signal=${signal})`),
        );
      }
    });
  });
}

async function runParallel(jobs) {
  console.log(`\n↗ parallel: ${jobs.map((j) => j.label).join(" ∥ ")}`);
  const results = await Promise.allSettled(
    jobs.map((j) => runAsync(j.label, j.cmd, j.args, j.opts)),
  );
  const failed = [];
  for (let i = 0; i < results.length; i++) {
    if (results[i].status === "rejected") {
      failed.push(jobs[i].label);
      console.error(results[i].reason);
    }
  }
  if (failed.length) {
    console.error(`\n✗ release:gate FAILED — ${failed.join(", ")}`);
    process.exit(1);
  }
}

async function runDesktopSection({ lite = false } = {}) {
  section("desktop");
  // Lint first so biome output is not interleaved; it is ~1s. On non-Windows,
  // tsc / vitest / conformance overlap; Win serializes to avoid tinypool IPC death.
  run("desktop lint", "pnpm", ["--filter", "agentcore-desktop", "lint"]);
  const desktopJobs = [
    {
      label: "desktop typecheck",
      cmd: "pnpm",
      args: ["--filter", "agentcore-desktop", "typecheck"],
    },
    {
      label: "desktop test",
      cmd: "pnpm",
      args: ["--filter", "agentcore-desktop", "exec", "vitest", "run"],
    },
    {
      label: "desktop conformance",
      cmd: "pnpm",
      args: ["--filter", "agentcore-desktop", "conformance"],
    },
  ];
  // Win: two vitest pools in one checkout tear down tinypool (ERR_IPC_CHANNEL_CLOSED).
  if (process.platform === "win32") {
    for (const job of desktopJobs) {
      run(job.label, job.cmd, job.args);
    }
  } else {
    await runParallel(desktopJobs);
  }
  if (lite) {
    console.log(
      "\n⏭ desktop shoot + smoke:webapp:ci skipped (--lite / RELEASE_GATE_LITE=1)",
    );
    return;
  }
  // Ops escape: skip screenshot matrix only; keep smoke:webapp:ci.
  if (process.env.RELEASE_GATE_SKIP_SHOOT === "1") {
    console.log(
      "\n⏭ desktop shoot skipped (RELEASE_GATE_SKIP_SHOOT=1); smoke:webapp:ci still runs",
    );
  } else {
    run("desktop shoot", "pnpm", ["--filter", "agentcore-desktop", "shoot"], {
      env: { SHOOT_FRAMES: "3" },
    });
  }
  // Pre-free smoke port so a leftover vite does not flake strictPort (port-fragile).
  const smokePort = String(process.env.SMOKE_PORT ?? "5175");
  run("free smoke port", "node", ["scripts/free-listen-port.mjs", smokePort]);
  run("desktop smoke:webapp:ci", "pnpm", [
    "--filter",
    "agentcore-desktop",
    "smoke:webapp:ci",
  ]);
}

const INTEGRATION_TESTS = "apps/server/tests/integration";

/** Print what a green gate did *not* cover, as the last thing on screen.
 *
 * The backend section excludes `tests/integration` (it needs a real Postgres most dev
 * boxes lack), and that directory is the only home of a number of contracts — so an
 * unqualified "✓ passed" reads as coverage the run never had. Naming the gap here is
 * the honest alternative to either failing dev boxes or letting the ✓ overclaim.
 */
function printIntegrationCoverageGap() {
  const count = listFiles(INTEGRATION_TESTS).filter((f) =>
    /[\\/]test_[^\\/]*\.py$/.test(f),
  ).length;
  const bar = "─".repeat(78);
  console.log(
    [
      "",
      bar,
      `⚠  未覆盖：后端 integration 套件（${count} 个测试文件）—— 本次 release:gate 未跑`,
      bar,
      "  backend 段的 pytest 带 --ignore=tests/integration（该套件需要真实 Postgres），",
      "  所以「✓ release:gate passed」并不等于全覆盖。只活在 integration 里的契约面（节选）：",
      "",
      "    · Stop API：POST /stop 幂等（false → true → false）；二次 Stop 取消在飞 worker，",
      "      每个 worker 发 run_cancelled(reason=stop)，且不得补派 _redir / _rev 跟进 run",
      "    · Cookie 会话 / CSRF / 限流 / 设备注册，以及各路由的 IDOR 归属校验（非属主 404）",
      "    · 落库与级联：turn journal（含清理）、run session 级联、stream state、paused turn",
      "    · 计费与配额：cost ledger、usage、quota / always 配额",
      "    · workspace / folders / 分享 / 导出 / 记忆管线 / admin 审计 的 HTTP 端到端",
      "",
      "  本地补跑（需要 Postgres；不跑不阻断日常开发）：",
      "    docker compose -f deploy/docker-compose.dev.yml up -d postgres",
      "    cd apps/server && uv run pytest tests/integration --tb=short",
      "",
      "  CI（ci.yml backend job）自带 Postgres 并跑全量 pytest；CI 上库不可达 = 直接失败，",
      "  不再静默跳过（见 apps/server/tests/integration/conftest.py）。",
      bar,
    ].join("\n"),
  );
}

async function main() {
  const filter = parseSectionArgs(process.argv);
  const partial = filter.from || filter.only;
  const modeBits = [];
  if (filter.lite) modeBits.push("LITE — skipped shoot/smoke; 发布仍需完整全量");
  if (partial) {
    modeBits.push(
      `PARTIAL: ${filter.only ? `only ${filter.only}` : `from ${filter.from}`} — 发布仍需全量绿`,
    );
  }
  console.log(
    `release:gate — local CI isomorphic gate${
      modeBits.length ? ` (${modeBits.join("; ")})` : ""
    }`,
  );

  const doContracts = sectionEnabled("contracts", filter);
  const doDesktop = sectionEnabled("desktop", filter);
  // Snapshot before ANY step of this run can touch a generated artifact — the drift
  // gate reads "did regen have to rewrite something?" off this baseline.
  const contractBaseline = doContracts ? await captureContractBaseline() : null;

  if (sectionEnabled("backend", filter)) {
    section("backend");
    run("ruff check", "uv", ["run", "ruff", "check", "."], { cwd: SERVER });
    run("mypy", "uv", ["run", "mypy"], { cwd: SERVER });
    // Migration head ↔ ORM metadata (offline). Catches DROP COLUMN/TABLE while
    // models/code still reference the old schema — the 2026-07-20 class of 500s.
    run(
      "schema gate",
      "uv",
      ["run", "python", "scripts/check_schema_gate.py"],
      { cwd: SERVER },
    );
    // Workspace hide rules are dual-sourced (Python _paths ↔ desktop
    // workspaceIgnore). Fail loudly when only one side is edited.
    run(
      "workspace ignore parity",
      "uv",
      ["run", "python", "scripts/check_workspace_ignore_parity.py"],
      { cwd: SERVER },
    );
    // Logger emit sites ↔ observability/catalog.py. Read-only: never rewrite.
    run(
      "log event catalog",
      "uv",
      ["run", "python", "scripts/sync_log_event_registry.py", "--check"],
      { cwd: SERVER },
    );
    run(
      "event consumer orphans",
      "uv",
      ["run", "python", "scripts/check_event_consumer_orphans.py"],
      { cwd: SERVER },
    );
    run(
      "event field consumers",
      "uv",
      ["run", "python", "scripts/check_event_field_consumers.py"],
      { cwd: SERVER },
    );
    // `-n auto` (pytest-xdist): wall-clock cut for ~5k unit tests; integration
    // stays serial/excluded here (shared DB). Override with PYTEST_XDIST_N=0 to
    // force single-process when hunting order flakes.
    const xdistN = process.env.PYTEST_XDIST_N ?? "auto";
    const pytestArgs = [
      "run",
      "pytest",
      "--ignore=tests/integration",
      "--tb=short",
      "-q",
    ];
    if (xdistN !== "0" && xdistN !== "false") {
      pytestArgs.push("-n", xdistN);
    }
    runLogged("pytest (unit)", "uv", pytestArgs, {
      cwd: SERVER,
      env: { LOG_LEVEL: "WARNING" },
    });
  }

  // Win: default serial — parallel contracts∥desktop often hits UNKNOWN open() on
  // api.generated.ts (same-checkout write race). Opt into parallel with
  // RELEASE_GATE_SERIAL=0. Nested --only children already force serial.
  if (
    process.platform === "win32" &&
    process.env.RELEASE_GATE_SERIAL === undefined
  ) {
    process.env.RELEASE_GATE_SERIAL = "1";
    console.log(
      "  (win32: RELEASE_GATE_SERIAL=1 default; set RELEASE_GATE_SERIAL=0 to parallel)",
    );
  }
  const parallelContractsDesktop =
    doContracts &&
    doDesktop &&
    !filter.only &&
    process.env.RELEASE_GATE_SERIAL !== "1";

  if (parallelContractsDesktop) {
    section("contracts ∥ desktop");
    console.log(
      "  (parallel children; set RELEASE_GATE_SERIAL=1 for sequential)",
    );
    // One upfront regen so desktop typecheck rarely races the contracts child's
    // first gen-types. Idempotence still re-regens inside the contracts child —
    // if that flakes, use RELEASE_GATE_SERIAL=1 (CI uses separate checkouts).
    // The child inherits THIS baseline: after the warm-up regen it could no longer
    // tell a stale artifact from an up-to-date one.
    const contractBaselinePath = writeContractBaseline(contractBaseline);
    regenContracts();
    await Promise.all([
      runSectionChild("contracts", { lite: filter.lite, contractBaselinePath }),
      runSectionChild("desktop", { lite: filter.lite }),
    ]);
  } else {
    if (doContracts) await runContractsSection(contractBaseline);
    if (doDesktop) await runDesktopSection({ lite: filter.lite });
  }

  if (sectionEnabled("mobile", filter)) {
    section("mobile");
    // Shared protocol predicates. Same placement as ci.yml mobile job —
    // keeps the extra seconds off the shoot-heavy desktop section.
    run("fold-kit test", "pnpm", ["--filter", "@agentcore/protocol-fold-kit", "test"]);
  }

  if (sectionEnabled("admin", filter)) {
    section("admin");
    run("admin typecheck", "pnpm", ["--filter", "agentcore-admin", "typecheck"]);
    run("admin test", "pnpm", ["--filter", "agentcore-admin", "exec", "vitest", "run"]);
  }

  if (filter.lite || partial) {
    const bits = [];
    if (filter.lite) bits.push("LITE");
    if (partial) {
      bits.push(filter.only ? `only ${filter.only}` : `from ${filter.from}`);
    }
    console.log(
      `\n✓ release:gate ${bits.join(" + ")} passed — 发布前仍需完整 pnpm release:gate（非 --lite）`,
    );
  } else {
    console.log("\n✓ release:gate passed");
  }

  // Last, so the coverage caveat is what stays on screen next to the ✓. Parallel
  // `--only contracts|desktop` children never enable backend, so it prints once.
  if (sectionEnabled("backend", filter)) printIntegrationCoverageGap();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
