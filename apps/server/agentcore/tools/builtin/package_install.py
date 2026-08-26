"""Cloud-controlled package install — network-layer registry allowlist (A) + cache (B).

Supports JS (npm/pnpm/yarn) and Python (uv/pip/poetry) with the same discipline.

- **A**：云端装包走桌上常驻 allowlist chokepoint（netns + proxy，与
  ``code_execute`` 同一 desk guest）；辅以 argv 形态白名单 + 固定包装源 env +
  拒绝改 registry/index 的 CLI 参数。本地（``backend.location=local``）不走主机
  gVisor 门禁，只钉源 + 权限轴。
- **B**：云端 ``install_cache_env`` → 沙箱 ``/pkg-cache``（OCI bind 到
  ``DATA_DIR/pkg-cache/<bucket>``）。工作区 rw-bind 为 ``/workspace``，
  ``node_modules`` / ``.venv`` 直接落在真盘。

Used by ``test_run`` (check=install / command=JS|Python install-shaped).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

# In-sandbox mount for package-manager caches (must match egress.PACKAGE_CACHE_MOUNT).
_PACKAGE_CACHE_MOUNT = "/pkg-cache"

# Official npm registry + common CN mirror. Pin via env; CLI overrides rejected.
ALLOWED_NPM_REGISTRIES: tuple[str, ...] = (
    "https://registry.npmjs.org/",
    "https://registry.npmmirror.com/",
)
DEFAULT_NPM_REGISTRY = ALLOWED_NPM_REGISTRIES[0]

# Egress-only packaging CDN hosts (unioned by egress/hosts.py). CDN ≠ 可改 registry：
# 仅出网放行 tarball/二进制拉取，勿当作可 pin / --registry 的包装源 URL。
# npm 官方 tarball 仍在 registry.npmjs.org（无独立 CDN 主机名），故不另列。
ALLOWED_NPM_HOSTS: tuple[str, ...] = (
    "cdn.npmmirror.com",
)

# Official PyPI simple index + common CN mirror. Pin via env; CLI overrides rejected.
ALLOWED_PYPI_REGISTRIES: tuple[str, ...] = (
    "https://pypi.org/simple/",
    "https://mirrors.aliyun.com/pypi/simple/",
)
DEFAULT_PYPI_INDEX = ALLOWED_PYPI_REGISTRIES[0]

# Egress-only wheel/sdist hosts (CDN ≠ pin index URL).
ALLOWED_PYPI_HOSTS: tuple[str, ...] = (
    "files.pythonhosted.org",
)

# Install / ci budget uses the same gVisor ceiling as other verify checks.
# Exposed so callers can document "aligned to sandbox max" without a second 60s trap.
INSTALL_NEEDS_RESTRICTED_NETWORK = True

_JS_INSTALL_VERBS = frozenset({"install", "ci", "i", "add"})
_JS_PM_BINS = frozenset({"npm", "pnpm", "yarn"})
_PY_PM_BINS = frozenset({"pip", "poetry", "uv"})
_PM_BINS = _JS_PM_BINS | _PY_PM_BINS
_PYTHON_LAUNCHERS = frozenset({"python", "python3", "py"})

# Flags that re-point the package source away from the pinned allowlist.
_REGISTRY_OVERRIDE_FLAGS = frozenset(
    {
        # JS
        "--registry",
        "--reg",
        "--npm-registry",
        "--npmregistryserver",
        # Python (pip / uv / poetry common index overrides)
        "-i",
        "--index-url",
        "--extra-index-url",
        "--find-links",
        "-f",
        "--index",
        "--default-index",
        "--publish-url",
        "--source",
    }
)

_REGISTRY_OVERRIDE_RE = re.compile(
    r"^(?:"
    r"--registry=.+"
    r"|--reg=.+"
    r"|--npm-registry=.+"
    r"|--npmRegistryServer=.+"
    r"|npmRegistryServer=.+"
    r"|registry=.+"
    r"|--@[A-Za-z0-9~._-]+:registry=.+"
    r"|-i=.+"
    r"|--index-url=.+"
    r"|--extra-index-url=.+"
    r"|--find-links=.+"
    r"|-f=.+"
    r"|--index=.+"
    r"|--default-index=.+"
    r"|--publish-url=.+"
    r"|--source=.+"
    r")$",
    re.IGNORECASE,
)

_SHELL_CHAIN_HINT_RE = re.compile(
    r"(?:&&|\|\||;|`|\$\(|^\s*(?:cd|pushd)\b)",
    re.IGNORECASE,
)

_NETWORK_DEGRADE_CODE = "install_network_unavailable"

_JS_DIR_FLAGS = frozenset({"--prefix", "--dir", "-c", "--cwd"})
# uv/poetry: ``-C`` / ``--directory`` (matched lowercased as ``-c``).
_PY_DIR_FLAGS = frozenset({"--directory", "-c", "--project"})


def is_install_shaped_argv(argv: list[str]) -> bool:
    """True for JS/Python install-shaped argv after optional safe dir flags."""
    pm, rest = _split_pm_and_rest(argv)
    if pm is None or not rest:
        return False
    return _rest_is_install_verb(pm, rest)


def install_prefix_allowed(argv: list[str]) -> bool:
    """Prefix match for ``_ALLOWED_PREFIXES``-style checks (pm + verb)."""
    return is_install_shaped_argv(argv)


def _rest_is_install_verb(pm: str, rest: list[str]) -> bool:
    verb = rest[0].lower()
    if pm in _JS_PM_BINS:
        return verb in _JS_INSTALL_VERBS
    if pm == "pip":
        return verb == "install"
    if pm == "poetry":
        return verb in {"install", "add"}
    if pm == "uv":
        # ``uv sync`` / ``uv add`` / ``uv pip install …``
        if verb in {"sync", "add"}:
            return True
        return verb == "pip" and len(rest) >= 2 and rest[1].lower() == "install"
    return False


def _split_pm_and_rest(argv: list[str]) -> tuple[str | None, list[str]]:
    """Return (pm, argv_after_optional_dir_flags) or (None, [])."""
    if not argv:
        return None, []
    head = argv[0].lower()
    start = 1
    pm: str | None
    # ``python -m pip …`` / ``python3 -m pip …``
    if head in _PYTHON_LAUNCHERS:
        if (
            len(argv) >= 3
            and argv[1].lower() == "-m"
            and argv[2].lower() == "pip"
        ):
            pm = "pip"
            start = 3
        else:
            return None, []
    elif head in _PM_BINS:
        pm = head
    else:
        return None, []

    i = start
    dir_flags = _JS_DIR_FLAGS if pm in _JS_PM_BINS else _PY_DIR_FLAGS
    # Safe subdirectory flags before the verb.
    while i < len(argv):
        flag = argv[i]
        flag_l = flag.lower()
        if flag_l in dir_flags and i + 1 < len(argv):
            if not is_safe_relpath(argv[i + 1]):
                return None, []
            i += 2
            continue
        if flag_l.startswith("--prefix=") or flag_l.startswith("--dir="):
            _, _, val = flag.partition("=")
            if not is_safe_relpath(val):
                return None, []
            i += 1
            continue
        if flag_l.startswith("--directory=") or flag_l.startswith("--project="):
            _, _, val = flag.partition("=")
            if not is_safe_relpath(val):
                return None, []
            i += 1
            continue
        break
    return pm, argv[i:]


def is_safe_relpath(raw: str) -> bool:
    """Workspace-relative path only: no abs, no ``..``, no empty / drive letters."""
    text = (raw or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or text.startswith("~"):
        return False
    if re.match(r"^[A-Za-z]:", text):
        return False
    # PurePosixPath(".").parts is () on some platforms — treat as workspace root.
    if text in (".", "./"):
        return True
    parts = PurePosixPath(text).parts
    return bool(parts) and ".." not in parts


def reject_shell_chain_command(command: str) -> str | None:
    """Refuse ``cd && npm/pip install`` / shell metacharacters in the raw command string."""
    text = (command or "").strip()
    if not text:
        return None
    if _SHELL_CHAIN_HINT_RE.search(text):
        return (
            "禁止用 shell 链（cd && / ; / ||）跑装包；"
            "请用 test_run check=install（可选 working_directory），"
            "或 check=command + `npm|pnpm|yarn install` / "
            "`uv sync` / `pip install` / `poetry install`，"
            "子目录用 --prefix / --dir / --directory / working_directory（相对路径）。"
        )
    return None


def reject_registry_override_argv(argv: list[str]) -> str | None:
    """Refuse CLI args that change the package registry / index (首版控制面)."""
    allow_hint = (
        f"JS: {', '.join(ALLOWED_NPM_REGISTRIES)}; "
        f"Python: {', '.join(ALLOWED_PYPI_REGISTRIES)}"
    )
    i = 0
    while i < len(argv):
        arg = argv[i]
        low = arg.lower()
        if low in _REGISTRY_OVERRIDE_FLAGS:
            return (
                f"禁止改包装源（检测到 {arg}）。"
                f"云端装包固定 allowlist registry（{allow_hint}）；"
                "勿传 --registry / --index-url / scope:registry / --source。"
            )
        if _REGISTRY_OVERRIDE_RE.match(arg):
            return (
                f"禁止改包装源（检测到 {arg}）。"
                f"云端装包固定 allowlist registry；"
                "勿传 --registry / --index-url / scope:registry / --source。"
            )
        i += 1
    return None


def validate_install_argv(argv: list[str]) -> str | None:
    """Return error message if install argv is unsafe; None if ok."""
    if not install_prefix_allowed(argv):
        return f"不是允许的装包形态：{' '.join(argv)}"
    reg_err = reject_registry_override_argv(argv)
    if reg_err:
        return reg_err
    # Re-validate any dir-flag values (also done in split)
    i = 0
    while i < len(argv):
        low = argv[i].lower()
        if low in (_JS_DIR_FLAGS | _PY_DIR_FLAGS) and i + 1 < len(argv):
            if not is_safe_relpath(argv[i + 1]):
                return (
                    f"装包子目录必须是工作区相对安全路径（禁止绝对路径 / ..）：{argv[i + 1]}"
                )
            i += 2
            continue
        if low.startswith(
            ("--prefix=", "--dir=", "--directory=", "--project=")
        ):
            _, _, val = argv[i].partition("=")
            if not is_safe_relpath(val):
                return f"装包子目录必须是工作区相对安全路径：{val}"
        i += 1
    return None


def registry_pin_env() -> dict[str, str]:
    """Env that pins common package managers to the default allowlisted registry.

    Complements the network-layer allowlist proxy (egress); argv overrides still rejected.
    """
    reg = DEFAULT_NPM_REGISTRY
    pypi = DEFAULT_PYPI_INDEX
    return {
        "NPM_CONFIG_REGISTRY": reg,
        "npm_config_registry": reg,
        "YARN_NPM_REGISTRY_SERVER": reg,
        "YARN_REGISTRY": reg,
        # pnpm reads npm_config_registry / NPM_CONFIG_REGISTRY
        "PNPM_REGISTRY": reg,
        # Python: pip / uv / poetry index pin
        "PIP_INDEX_URL": pypi,
        "UV_INDEX_URL": pypi,
        "UV_DEFAULT_INDEX": pypi,
        "POETRY_PYPI_MIRROR_URL": pypi,
    }


def install_cache_env() -> dict[str, str]:
    """B 预缓存：指向沙箱内 ``/pkg-cache``（OCI bind 到 DATA_DIR 分桶目录）。"""
    root = _PACKAGE_CACHE_MOUNT
    return {
        "NPM_CONFIG_CACHE": f"{root}/npm",
        "npm_config_cache": f"{root}/npm",
        "YARN_CACHE_FOLDER": f"{root}/yarn",
        "PNPM_STORE_PATH": f"{root}/pnpm",
        "PIP_CACHE_DIR": f"{root}/pip",
        "UV_CACHE_DIR": f"{root}/uv",
        "POETRY_CACHE_DIR": f"{root}/poetry",
    }


def network_unavailable_message() -> str:
    """甲降级：无法装包时的诚实说明（勿空转跑 install）。"""
    return (
        "无法装包：当前会话未授权受限出网，或云端主机不具备包装源白名单出网能力"
        "（云端需 Linux gVisor + netns chokepoint；本机执行不依赖主机 gVisor）。"
        "装包不会在无授权 / 无 chokepoint 时空转。\n"
        "可选降级：① 将命令执行轴设为 auto 后重试 test_run check=install "
        "→ build；② 走结构自检（graph_consistent / import 图）；"
        "③ export_to_local 或本机传统打开本地文件夹后 npm/pnpm/yarn / uv/pip/poetry "
        "install（已是云端会话时【勿】再引导「导入到云」当修复）。"
    )


def network_unavailable_code() -> str:
    return _NETWORK_DEGRADE_CODE


def resolve_install_argv(
    *,
    package_managers: list[str],
    working_directory: str | None = None,
) -> list[str]:
    """Build default install argv from workspace profile.

    Pure Python workspaces must not fall through to ``npm install``.
    Mixed JS+Python keeps JS-first (monorepo root install). Empty → npm (legacy).
    """
    pms = list(package_managers or [])
    js_pm: str | None = None
    for candidate in ("pnpm", "yarn", "npm"):
        if candidate in pms:
            js_pm = candidate
            break
    py_pm: str | None = None
    for candidate in ("uv", "poetry", "pip"):
        if candidate in pms:
            py_pm = candidate
            break

    if py_pm and not js_pm:
        return _resolve_python_install_argv(py_pm, working_directory)
    pm = js_pm or "npm"
    argv = [pm, "install"]
    wd = (working_directory or "").strip()
    if wd:
        if pm == "npm":
            argv = ["npm", "--prefix", wd, "install"]
        elif pm == "pnpm":
            argv = ["pnpm", "--dir", wd, "install"]
        else:
            argv = ["yarn", "--cwd", wd, "install"]
    return argv


def _resolve_python_install_argv(pm: str, working_directory: str | None) -> list[str]:
    wd = (working_directory or "").strip()
    if pm == "uv":
        argv = ["uv", "sync"]
        if wd:
            argv = ["uv", "--directory", wd, "sync"]
        return argv
    if pm == "poetry":
        argv = ["poetry", "install"]
        if wd:
            argv = ["poetry", "--directory", wd, "install"]
        return argv
    # pip
    if wd:
        return ["pip", "install", "-r", f"{wd}/requirements.txt"]
    return ["pip", "install", "-r", "requirements.txt"]


def apply_working_directory(argv: list[str], working_directory: str | None) -> list[str]:
    """Inject safe subdirectory flags when tool param is set and argv lacks one."""
    wd = (working_directory or "").strip()
    if not wd or not argv:
        return argv

    # ``python -m pip …`` — rewrite -r path or leave verb-only install as-is
    head = argv[0].lower()
    if head in _PYTHON_LAUNCHERS:
        if (
            len(argv) >= 4
            and argv[1].lower() == "-m"
            and argv[2].lower() == "pip"
        ):
            return _apply_pip_working_directory(argv, wd, pip_at=2)
        return argv

    pm = head
    if pm not in _PM_BINS:
        return argv

    # Already has a dir flag
    for a in argv[1:]:
        low = a.lower()
        if low in (_JS_DIR_FLAGS | _PY_DIR_FLAGS) or low.startswith(
            ("--prefix=", "--dir=", "--directory=", "--project=")
        ):
            return argv

    if pm == "npm":
        return ["npm", "--prefix", wd, *argv[1:]]
    if pm == "pnpm":
        return ["pnpm", "--dir", wd, *argv[1:]]
    if pm == "yarn":
        return ["yarn", "--cwd", wd, *argv[1:]]
    if pm == "uv":
        return ["uv", "--directory", wd, *argv[1:]]
    if pm == "poetry":
        return ["poetry", "--directory", wd, *argv[1:]]
    if pm == "pip":
        return _apply_pip_working_directory(argv, wd, pip_at=0)
    return argv


def _apply_pip_working_directory(
    argv: list[str], wd: str, *, pip_at: int
) -> list[str]:
    """Prefix ``-r`` requirements path with ``wd/`` when present; else leave argv."""
    out = list(argv)
    i = pip_at + 1
    while i < len(out):
        if out[i] in ("-r", "--requirement") and i + 1 < len(out):
            req = out[i + 1]
            if not req.startswith(f"{wd}/") and is_safe_relpath(req):
                out[i + 1] = f"{wd}/{req}"
            return out
        if out[i].startswith("--requirement="):
            _, _, val = out[i].partition("=")
            if val and not val.startswith(f"{wd}/") and is_safe_relpath(val):
                out[i] = f"--requirement={wd}/{val}"
            return out
        i += 1
    return out
