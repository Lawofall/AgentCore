// @ts-check
/*
 * 构建内置 Python 运行时（双模式工作区 §十「内置 Python 打包」，方案 B）。
 *
 * 目标：让**打包后**的桌面端无需用户机器上的任何系统 Python / venv / uv，也能拉起
 * `python -m agentcore.sidecar`（本机 Python 引擎）。做法 = 随包带：
 *   1) 一份**独立 CPython 发行版**（python-build-standalone，uv 同源、设计上可重定位）；
 *   2) 一份用 `uv pip install --target` 装好的旁路 site-packages——只装 sidecar **运行时
 *      子集**（pyproject 的 `[project.optional-dependencies].sidecar`）+ `--no-deps` 的
 *      agentcore 包本体，而非整个 server。剔掉 FastAPI/uvicorn/alembic/redis/boto3/jose/
 *      cryptography 等不在 sidecar 回合路径上的重依赖；**保留**与云对齐的 Office 栈
 *      （markitdown[docx,pdf,pptx] + python-docx + fpdf2 + markdown-it-py）。
 *      回合路径（``git_execution_enabled_for``）禁止再拉 pwdlib / python-jose：构建冒烟
 *      会在捆绑解释器里断言这两个模块未进 ``sys.modules``。缺依赖时修导入闭包，不要把
 *      密码学栈加回 sidecar extra。
 * 运行期主进程 `resolveSpawnConfig`（`src/main/sidecar-service.ts`）在 `app.isPackaged` 时指向
 * `<resources>/sidecar/python` 的解释器，并以 `PYTHONPATH=<resources>/sidecar/site-packages`
 * 注入引擎包——用 `--target` 旁路目录而非 venv，绕开「venv 记录的 base python 绝对路径在用户机
 * 器上不存在」的重定位之痛。
 *
 * 产物：`apps/desktop/resources/sidecar/{python, site-packages}`（被 .gitignore 忽略），
 * 由 electron-builder `extraResources` 拷进安装包（见 electron-builder.config.mjs）。
 *
 * **平台约束**：原生 wheel / 解释器无法交叉编译，故本脚本只为**当前运行平台**构建；三平台产物
 * 由各自的 CI runner 分别跑 `pnpm build:<os>`（package.json 已把本脚本前置）。
 *
 * 行为零漂移：内置版本对齐 dev 的 server `.venv`（当前 3.13），用 `uv pip install --python
 * <内置解释器>` 解析 wheel，使 ABI 与最终运行的解释器完全一致。
 *
 * 用法：`pnpm bundle:sidecar`（或 `node scripts/bundle-sidecar.mjs`）。需 PATH 上有 `uv`。
 */
import { execFileSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  symlinkSync,
  unlinkSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

/** 内置 CPython 版本（对齐 dev server `.venv`；改这里即整体换版）。 */
const PYTHON_VERSION = "3.13";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const serverDir = resolve(desktopDir, "..", "server");
const outDir = join(desktopDir, "resources", "sidecar");
const pythonDir = join(outDir, "python");
const sitePackages = join(outDir, "site-packages");
const isWin = process.platform === "win32";

/** 内置解释器在拷贝后发行版中的相对位置（python-build-standalone 布局，按平台固定）。 */
function bundledPythonExe() {
  return isWin
    ? join(pythonDir, "python.exe")
    : join(pythonDir, "bin", "python3");
}

/**
 * unix：修复 `python/bin/` 里指向包外绝对路径的 symlink。
 *
 * `cpSync(..., { dereference: true })` 在部分 Node/平台组合下仍会保留指向 uv 缓存
 * （如 `/Users/runner/.local/share/uv/python/...`）的绝对 link。用户机上该路径不存在，
 * 主进程 spawn `python3` 必挂。策略：凡 link 目标落在 `pythonDir` 之外，改为指向同目录
 * 真实 `pythonX.Y`（优先与 PYTHON_VERSION 对齐）的相对 symlink。
 */
function fixUnixPythonBinLinks() {
  if (isWin) return;
  const binDir = join(pythonDir, "bin");
  if (!existsSync(binDir)) return;

  const versionedName = `python${PYTHON_VERSION}`;
  const versionedPath = join(binDir, versionedName);
  if (!existsSync(versionedPath)) {
    throw new Error(
      `unix 内置发行版缺少真实解释器 ${versionedPath}，无法修复 bin/ symlink`,
    );
  }

  const pythonDirReal = realpathSync(pythonDir);
  for (const name of readdirSync(binDir)) {
    const linkPath = join(binDir, name);
    let st;
    try {
      st = lstatSync(linkPath);
    } catch {
      continue;
    }
    if (!st.isSymbolicLink()) continue;

    let target;
    try {
      target = readlinkSync(linkPath);
    } catch {
      continue;
    }
    const resolved = isAbsolute(target)
      ? resolve(target)
      : resolve(binDir, target);

    // 目标已在包内 → 保留（可再规范成相对 link，但非必须）
    let inside = false;
    try {
      inside = isPathInside(pythonDirReal, realpathSync(resolved));
    } catch {
      // 断链或目标不存在 → 视为包外，强制重写
      inside = false;
    }
    if (inside) continue;

    console.log(
      `修复包外 symlink: ${name} -> ${target}  =>  相对 ${versionedName}`,
    );
    unlinkSync(linkPath);
    symlinkSync(versionedName, linkPath);
  }
}

/** `candidate` 是否严格落在 `root` 目录树之下（不含 root 自身）。 */
function isPathInside(root, candidate) {
  const rel = relative(root, candidate);
  return (
    rel !== "" &&
    rel !== ".." &&
    !rel.startsWith(`..${sep}`) &&
    !isAbsolute(rel)
  );
}

/**
 * 硬门禁：`bundledExe` 经 realpath 后必须落在 `pythonDir` 树内。
 * 否则说明仍残留指向 CI/构建机绝对路径的 symlink，构建必须失败。
 */
function assertBundledPythonInsideTree(bundledExe) {
  const pythonDirReal = realpathSync(pythonDir);
  let exeReal;
  try {
    exeReal = realpathSync(bundledExe);
  } catch (err) {
    throw new Error(
      `内置解释器无法解析（疑似断链 symlink）: ${bundledExe}\n` +
        `  ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  if (!isPathInside(pythonDirReal, exeReal)) {
    throw new Error(
      `内置解释器 realpath 落在 pythonDir 之外（禁止打进安装包）:\n` +
        `  exe:      ${bundledExe}\n` +
        `  realpath: ${exeReal}\n` +
        `  pythonDir:${pythonDirReal}\n` +
        `  （常见原因：bin/python3 仍是指向 /Users/runner/... 的绝对 symlink）`,
    );
  }
}

function run(cmd, args, opts = {}) {
  console.log(`$ ${cmd} ${args.join(" ")}`);
  execFileSync(cmd, args, { stdio: "inherit", ...opts });
}

function capture(cmd, args) {
  return execFileSync(cmd, args, { encoding: "utf-8" }).trim();
}

function main() {
  if (!existsSync(serverDir)) {
    throw new Error(`未找到服务端目录: ${serverDir}`);
  }

  // 1. 清理上次产物（确保可重复构建）。
  console.log(`清理 ${outDir}`);
  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });

  // 2. 确保 uv 已拉到目标版本的独立 CPython。
  run("uv", ["python", "install", PYTHON_VERSION]);

  // 3. 定位它，推出发行版根目录。
  //    win:  <distRoot>/python.exe       → distRoot = dirname(exe)
  //    unix: <distRoot>/bin/python3      → distRoot = dirname(dirname(exe))
  const exe = capture("uv", ["python", "find", PYTHON_VERSION]);
  if (!exe || !existsSync(exe)) {
    throw new Error(`uv python find 未返回可用解释器: ${exe || "(空)"}`);
  }
  const distRoot = isWin ? dirname(exe) : dirname(dirname(exe));

  // 4. 拷贝整份发行版（含 stdlib / DLL；python-build-standalone 设计上可重定位）。
  //    dereference：尽量解开 unix 下的 python3 → python3.x 等符号链接。
  //    实测 Mac CI 上仍可能留下指向 uv 缓存绝对路径的 symlink（如
  //    /Users/runner/.local/share/uv/python/...）——用户机上必断，故拷贝后强制修复并门禁。
  console.log(`拷贝 Python 运行时:\n  ${distRoot}\n  -> ${pythonDir}`);
  cpSync(distRoot, pythonDir, { recursive: true, dereference: true });
  fixUnixPythonBinLinks();

  const bundledExe = bundledPythonExe();
  if (!existsSync(bundledExe)) {
    throw new Error(
      `拷贝后未找到内置解释器: ${bundledExe}\n` +
        `（发行版布局与预期不符，请核对 uv python find 的输出: ${exe}）`,
    );
  }
  assertBundledPythonInsideTree(bundledExe);

  // 5. 装**运行时子集**（而非整个 server）到旁路 site-packages（--target）。分两条命令：
  //    a) 先装 sidecar 依赖组（含其传递依赖）；b) 再 --no-deps 装 agentcore 包本体。
  //    必须分开：--no-deps 会**同时**跳过显式依赖的传递依赖（如 httpx→httpcore/anyio），
  //    故它只用于「装包代码、不要它的 deps」，依赖另由 (a) 完整解析。--python 指向内置
  //    解释器，使 wheel 的 ABI 与最终运行的解释器一致（而非构建机当前解释器）。
  const sidecarDeps = readSidecarDeps(serverDir, bundledExe);
  console.log(`安装 sidecar 运行时子集（${sidecarDeps.length} 个直接依赖）`);
  run("uv", [
    "pip",
    "install",
    "--python",
    bundledExe,
    "--target",
    sitePackages,
    ...sidecarDeps,
  ]);
  console.log("安装 agentcore 包本体（--no-deps）");
  run("uv", [
    "pip",
    "install",
    "--python",
    bundledExe,
    "--target",
    sitePackages,
    "--no-deps",
    serverDir,
  ]);

  // 6. 剔除运行期用不到的产物，进一步瘦身：
  //    - 旁路 site-packages 的 `bin/` 控制台脚本壳（agentcore.exe / httpx.exe …）——sidecar
  //      只跑 `-m agentcore.sidecar`，从不经这些壳；
  //    - 内置 CPython 自带的 `pip`（python-build-standalone 随发行版带）——运行期不装包。
  pruneBundle();

  // 7. 冒烟自检：sidecar 入口 + Office 栈（与云同能力；缺 docx 曾导致本地回合 PIPELINE_ERROR）。
  console.log("冒烟自检: import agentcore.sidecar.server");
  run(
    bundledExe,
    ["-c", "import agentcore.sidecar.server; print('sidecar import OK')"],
    { env: { ...process.env, PYTHONPATH: sitePackages, PYTHONUTF8: "1" } },
  );
  console.log("冒烟自检: 回合路径不得拉入 pwdlib / python-jose");
  run(
    bundledExe,
    [
      "-c",
      [
        "import sys",
        "from agentcore.tools.builtin import git_execution_enabled_for",
        "class _B:",
        "    location = 'local'",
        "    root = '.'",
        "git_execution_enabled_for(_B(), desktop_online=True)",
        "blocked = ('pwdlib', 'jose', 'agentcore.security.passwords', 'agentcore.security.tokens')",
        "hit = [n for n in blocked if n in sys.modules]",
        "assert not hit, hit",
        "print('sidecar turn-path import OK')",
      ].join("\n"),
    ],
    { env: { ...process.env, PYTHONPATH: sitePackages, PYTHONUTF8: "1" } },
  );
  console.log("冒烟自检: Office literacy (docx + pdf + markitdown extract)");
  run(
    bundledExe,
    [
      "-c",
      [
        "from io import BytesIO",
        "from docx import Document",
        "from markitdown import MarkItDown",
        "from agentcore.docs_export.md_to_docx import convert_markdown_to_docx",
        "from agentcore.docs_export.md_to_pdf import convert_markdown_to_pdf",
        "d=Document(); d.add_paragraph('AgentCore sidecar office smoke'); buf=BytesIO(); d.save(buf)",
        "text=MarkItDown(enable_plugins=False).convert_stream(BytesIO(buf.getvalue()), file_extension='.docx').text_content or ''",
        "assert 'sidecar office smoke' in text, text[:200]",
        "out=convert_markdown_to_docx('# Hello\\n\\nworld')",
        "assert out.docx_bytes.startswith(b'PK'), len(out.docx_bytes)",
        "pdf=convert_markdown_to_pdf('# Hello\\n\\nworld')",
        "assert pdf.pdf_bytes.startswith(b'%PDF'), len(pdf.pdf_bytes)",
        "print('sidecar office OK')",
      ].join("; "),
    ],
    { env: { ...process.env, PYTHONPATH: sitePackages, PYTHONUTF8: "1" } },
  );

  // 8. 内嵌 ripgrep（产品 AI grep；主进程 opGrep + sidecar AGENTCORE_RG_PATH 共用）。
  fetchDesktopRipgrep();

  console.log(`\n✅ 内置 Python 运行时就绪: ${outDir}`);
}

/** 下载当前平台 rg 到 resources/rg/（与 electron-builder extraResources 对齐）。 */
function fetchDesktopRipgrep() {
  const fetchScript = join(serverDir, "scripts", "fetch_ripgrep.py");
  if (!existsSync(fetchScript)) {
    throw new Error(`缺少 ripgrep 获取脚本: ${fetchScript}`);
  }
  console.log("获取内嵌 ripgrep → resources/rg/");
  // Prefer server venv python; fall back to uv run.
  const venvPy = isWin
    ? join(serverDir, ".venv", "Scripts", "python.exe")
    : join(serverDir, ".venv", "bin", "python");
  if (existsSync(venvPy)) {
    run(venvPy, [fetchScript, "--install-desktop"], { cwd: serverDir });
  } else {
    run("uv", ["run", "python", fetchScript, "--install-desktop"], {
      cwd: serverDir,
    });
  }
  const rgName = isWin ? "rg.exe" : "rg";
  const rgPath = join(desktopDir, "resources", "rg", rgName);
  if (!existsSync(rgPath)) {
    throw new Error(`ripgrep 未写入预期路径: ${rgPath}`);
  }
}

/**
 * 读取 pyproject 的 `[project.optional-dependencies].sidecar`（运行时子集的**单一真相源**）。
 * 用**内置解释器自带的 stdlib `tomllib`**（3.11+）解析，避免在 JS 里引第三方 TOML 解析器、
 * 也避免依赖列表在两处漂移。
 */
function readSidecarDeps(serverDir, bundledExe) {
  const pyproject = join(serverDir, "pyproject.toml");
  const code =
    "import tomllib,sys;" +
    "d=tomllib.load(open(sys.argv[1],'rb'));" +
    "print(chr(10).join(d['project']['optional-dependencies']['sidecar']))";
  const out = capture(bundledExe, ["-c", code, pyproject]);
  const deps = out
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (deps.length === 0) {
    throw new Error(
      `pyproject 缺少 [project.optional-dependencies].sidecar 或为空: ${pyproject}`,
    );
  }
  return deps;
}

/** 删一个目录（存在才删），打印动作。 */
function dropDir(dir, label) {
  if (existsSync(dir)) {
    console.log(`剔除 ${label}: ${dir}`);
    rmSync(dir, { recursive: true, force: true });
  }
}

/**
 * 剔除运行期用不到的产物（纯瘦身，best-effort）：
 *   - `<site-packages>/bin`：console_scripts 壳，sidecar 走 `-m` 不用；
 *   - 内置发行版自带的 `pip`（含 `pip-*.dist-info`）：运行期不装包。
 * pip 的位置随平台不同（win: `Lib/site-packages`；unix: `lib/python<ver>/site-packages`），
 * 找不到只跳过、不报错（瘦身失败不应中断构建）。
 */
function pruneBundle() {
  dropDir(join(sitePackages, "bin"), "site-packages/bin 控制台脚本壳");

  const stdlibSite = isWin
    ? join(pythonDir, "Lib", "site-packages")
    : join(pythonDir, "lib", `python${PYTHON_VERSION}`, "site-packages");
  if (!existsSync(stdlibSite)) return;
  for (const name of readdirSync(stdlibSite)) {
    if (name === "pip" || name.startsWith("pip-")) {
      dropDir(join(stdlibSite, name), `内置 pip（${name}）`);
    }
  }
}

main();
