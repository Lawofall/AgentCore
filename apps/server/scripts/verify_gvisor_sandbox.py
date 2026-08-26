#!/usr/bin/env python3
"""gVisor 沙箱灰度 — Linux / Docker 集成验证脚本。

开发机 Windows 跑不了真 runsc；本脚本分两档：

1. **本机语法 / 边界自检**（任何 OS，默认）：
   ``python apps/server/scripts/verify_gvisor_sandbox.py``
   覆盖：OCI config 形状、settings 灰度默认值、
   Dockerfile / compose / env 样例资产是否在仓。

2. **真沙箱冒烟**（Linux + runsc，或 Docker 容器内）：
   ``python apps/server/scripts/verify_gvisor_sandbox.py --live``
   需要 PATH 上有 ``runsc``（或 ``GVISOR_RUNSC_PATH``）。会真实 ``runsc run -d``
   再 ``exec`` 一段 python，产物 bind 落在临时工作区。

Docker 用法（推荐在生产镜像上验证，不在 Windows 宿主机）：

验收身份必须是生产身份（``USER app`` + 已起的 ``sandboxd``）。
**禁止** ``--user 0`` 直跑本脚本冒烟当放行——API 只走 Unix 客户端，
root 直跑 ``runsc`` 不能代表生产。

.. code-block:: bash

   # 叠 sandbox overlay 后进入已在跑的 api 容器（entrypoint 已起 sandboxd 并降为 app）
   docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml \\
     exec api python apps/server/scripts/verify_gvisor_sandbox.py --live

退出码：0 = 通过；非 0 = 失败（CI / 人工清单可直接看）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path


def _resolve_roots() -> tuple[Path, Path]:
    """Resolve monorepo vs api-image layout (``/app`` without ``apps/server`` prefix)."""
    script = Path(__file__).resolve()
    server_candidate = script.parents[1]
    if (server_candidate / "agentcore").is_dir():
        repo = server_candidate
        for candidate in (server_candidate.parent, server_candidate.parent.parent):
            if (candidate / "deploy").is_dir():
                repo = candidate
                break
        return repo, server_candidate
    return script.parents[3], script.parents[3] / "apps" / "server"


REPO_ROOT, SERVER_ROOT = _resolve_roots()


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    print(f" FAIL {msg}", file=sys.stderr)


def check_repo_assets() -> list[str]:
    """Assets that must exist before a gray release is even attempted."""
    errors: list[str] = []
    deploy_root = REPO_ROOT / "deploy"
    if not deploy_root.is_dir():
        print("  SKIP repo asset checks (not a full monorepo checkout / deploy/)")
        required = [
            SERVER_ROOT / "agentcore" / "tools" / "sandbox" / "gvisor.py",
            SERVER_ROOT / "agentcore" / "tools" / "sandbox" / "limits.py",
        ]
        for path in required:
            if path.is_file():
                _ok(f"asset {path.name}")
            else:
                errors.append(f"missing {path}")
                _fail(f"missing {path}")
        return errors

    required = [
        SERVER_ROOT / "Dockerfile",
        SERVER_ROOT / "scripts" / "fetch_runsc.py",
        SERVER_ROOT / "agentcore" / "tools" / "sandbox" / "gvisor.py",
        SERVER_ROOT / "agentcore" / "tools" / "sandbox" / "limits.py",
        REPO_ROOT / "deploy" / "docker-compose.sandbox.yml",
        REPO_ROOT / "deploy" / "api-sandbox-entrypoint.sh",
        REPO_ROOT / "deploy" / "config" / "production.env.example",
    ]
    for path in required:
        if path.is_file():
            _ok(f"asset {path.relative_to(REPO_ROOT)}")
        else:
            errors.append(f"missing {path}")
            _fail(f"missing {path.relative_to(REPO_ROOT)}")

    dockerfile = (SERVER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    for needle in ("runsc", "cloud_python.txt", "fonts-noto-cjk", "INSTALL_RUNSC"):
        if needle in dockerfile:
            _ok(f"Dockerfile mentions {needle}")
        else:
            errors.append(f"Dockerfile missing {needle}")
            _fail(f"Dockerfile missing {needle}")

    sandbox_yml = (REPO_ROOT / "deploy" / "docker-compose.sandbox.yml").read_text(
        encoding="utf-8"
    )
    for needle in (
        "NET_ADMIN",
        "SYS_ADMIN",
        "api-sandbox-entrypoint.sh",
        'user: "0:0"',
    ):
        if needle in sandbox_yml:
            _ok(f"docker-compose.sandbox.yml has {needle}")
        else:
            errors.append(f"docker-compose.sandbox.yml missing {needle}")
            _fail(f"docker-compose.sandbox.yml missing {needle}")

    env_ex = (REPO_ROOT / "deploy" / "config" / "production.env.example").read_text(
        encoding="utf-8"
    )
    for key in (
        "GVISOR_ENABLED",
        "GVISOR_MAX_CONCURRENT_EXECUTIONS",
        "GVISOR_TIMEOUT_MAX_SECONDS",
        "GVISOR_DESK_IDLE_TTL_SECONDS",
    ):
        if key in env_ex:
            _ok(f"production.env.example has {key}")
        else:
            errors.append(f"production.env.example missing {key}")
            _fail(f"production.env.example missing {key}")
    return errors


def check_settings_defaults() -> list[str]:
    sys.path.insert(0, str(SERVER_ROOT))
    from agentcore.config import settings  # noqa: WPS433
    from agentcore.config.workspace import WorkspaceSettings  # noqa: WPS433

    errors: list[str] = []
    expected = {
        "gvisor_enabled": False,
        "gvisor_max_concurrent_executions": 2,
        "gvisor_slot_wait_seconds": 15.0,
        "gvisor_memory_limit_mb": 512,
        "gvisor_timeout_max_seconds": 60,
    }
    for attr, want in expected.items():
        got = getattr(settings, attr)
        if got != want:
            errors.append(f"settings.{attr}={got!r} want {want!r}")
            _fail(f"settings.{attr}={got!r} (expected {want!r})")
        else:
            _ok(f"settings.{attr}={got!r}")

    root_default = WorkspaceSettings.model_fields["gvisor_runtime_root"].default
    if root_default == "/tmp/agentcore-sandbox":
        errors.append("gvisor_runtime_root still defaults to /tmp legacy")
        _fail("gvisor_runtime_root still defaults to /tmp legacy")
    elif "tmp" in str(root_default).replace("\\", "/").split("/"):
        errors.append(f"gvisor_runtime_root default looks tmp-based: {root_default!r}")
        _fail(f"gvisor_runtime_root default looks tmp-based: {root_default!r}")
    else:
        _ok(f"gvisor_runtime_root default={root_default!r}")
    return errors


def check_oci_config_shape() -> list[str]:
    sys.path.insert(0, str(SERVER_ROOT))
    from agentcore.tools.sandbox.gvisor import GVisorSandbox  # noqa: WPS433

    errors: list[str] = []
    sandbox = GVisorSandbox(runtime_root=tempfile.mkdtemp(prefix="gvisor_oci_"))
    cache = tempfile.mkdtemp(prefix="gvisor_cache_")
    cfg = sandbox._build_desk_oci(  # noqa: SLF001
        workspace="/tmp/ws",
        scratch_dir="/tmp/scratch",
        netns_path="/var/run/netns/acpkg1",
        cache_host_dir=cache,
        proxy_url="http://10.0.0.1:8898",
        memory_limit_mb=512,
    )
    try:
        json.dumps(cfg)
    except TypeError as e:
        errors.append(f"OCI config not JSON-serializable: {e}")
        _fail(str(e))
        return errors

    mounts = {m["destination"]: m for m in cfg["mounts"]}
    if mounts.get("/workspace", {}).get("type") != "bind":
        errors.append("/workspace should be a rw bind of the real workspace")
        _fail("/workspace mount is not bind")
    elif "rw" not in mounts.get("/workspace", {}).get("options", []):
        errors.append("/workspace should be writable")
        _fail("/workspace mount not writable")
    else:
        _ok("OCI /workspace is rw-bind (cloud desk)")
    uid = cfg.get("process", {}).get("user", {}).get("uid")
    if uid == 65534:
        errors.append("desk OCI uid is nobody (65534)")
        _fail("desk OCI uid 65534")
    else:
        _ok(f"OCI uid={uid} (not 65534)")
    if cfg["process"]["cwd"] != "/workspace":
        errors.append("cwd != /workspace")
        _fail("process.cwd != /workspace")
    else:
        _ok("OCI cwd=/workspace")
    return errors


async def check_live_runsc() -> list[str]:
    """Real runsc smoke — Linux only. Writes a small file via python in the sandbox."""
    sys.path.insert(0, str(SERVER_ROOT))
    import agentcore.tools.sandbox.gvisor as gvisor_mod  # noqa: WPS433
    from agentcore.config import settings  # noqa: WPS433
    from agentcore.tools.sandbox.gvisor import GVisorSandbox  # noqa: WPS433
    from agentcore.tools.sandbox.protocol import ExecutionRequest  # noqa: WPS433

    errors: list[str] = []
    if not gvisor_mod._IS_LINUX:  # noqa: SLF001
        errors.append("live mode requires Linux")
        _fail("live mode requires Linux (use Docker on the api image)")
        return errors

    runsc = settings.gvisor_runsc_path
    sandbox = GVisorSandbox(runsc_path=runsc)
    if not await sandbox.health_check():
        errors.append(f"runsc health_check failed ({runsc})")
        _fail(f"runsc not healthy: {runsc}")
        return errors
    _ok(f"runsc health_check via {runsc}")

    with tempfile.TemporaryDirectory(prefix="gvisor_live_") as td:
        ws = Path(td) / "ws"
        ws.mkdir()
        code = (
            "from pathlib import Path\n"
            "Path('live.txt').write_text('gvisor-live-ok', encoding='utf-8')\n"
            "print('ok')\n"
        )
        result = await sandbox.execute(
            ExecutionRequest(
                code=code,
                language="python",
                cwd=str(ws),
                timeout_seconds=30,
            )
        )
        if not result.success:
            errors.append(f"live execute failed: {result.stderr!r}")
            _fail(f"live execute failed exit={result.exit_code} stderr={result.stderr!r}")
            return errors
        if "live.txt" not in (result.written_files or []):
            errors.append(f"written_files={result.written_files}")
            _fail(f"expected live.txt in written_files, got {result.written_files}")
        landed = ws / "live.txt"
        if not landed.is_file() or landed.read_text(encoding="utf-8") != "gvisor-live-ok":
            errors.append("artifact missing after live execute")
            _fail(f"artifact missing or wrong: {landed}")
        else:
            _ok("live runsc execute bind-writes live.txt")

        # Optional: document libs present in the sandbox image (best-effort).
        lib_check = await sandbox.execute(
            ExecutionRequest(
                code=(
                    "import importlib.util\n"
                    "libs=('pptx','docx','openpyxl','matplotlib','pandas')\n"
                    "missing=[n for n in libs if importlib.util.find_spec(n) is None]\n"
                    "print('missing=' + ','.join(missing) if missing else 'all-present')\n"
                ),
                language="python",
                cwd=str(ws),
                timeout_seconds=30,
            )
        )
        if lib_check.success and "all-present" in lib_check.stdout:
            _ok("sandbox python has pptx/docx/openpyxl/matplotlib/pandas")
        elif lib_check.success:
            # Not a hard failure on bare host (no image libs); warn via FAIL only if --strict-libs
            print(f"  WARN sandbox libs: {lib_check.stdout.strip()}")
        else:
            print(f"  WARN lib probe failed: {lib_check.stderr.strip()}")
    return errors


async def check_live_desk_health() -> list[str]:
    """Same predicate as cloud Chromium assembly: desk ``cloud_sandbox_health``."""
    sys.path.insert(0, str(SERVER_ROOT))
    from agentcore.config import settings  # noqa: WPS433
    from agentcore.tools.sandbox.cloud_health import (  # noqa: WPS433
        cloud_sandbox_health,
        probe_cloud_sandbox_at_startup,
        reset_cloud_sandbox_health_for_tests,
    )

    errors: list[str] = []
    if not settings.gvisor_enabled:
        print("  SKIP desk health probe (gvisor_enabled=false)")
        return errors
    reset_cloud_sandbox_health_for_tests()
    await probe_cloud_sandbox_at_startup()
    if cloud_sandbox_health() is not False:
        _ok("cloud desk guest health")
        return errors
    errors.append("cloud desk guest unhealthy")
    _fail(
        "cloud_sandbox_health failed — stack docker-compose.sandbox.yml "
        "(entrypoint starts sandboxd, then USER app); "
        "do not fall back to DesktopBridge / a second browser jail"
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run a real runsc execute smoke (Linux / Docker only)",
    )
    args = parser.parse_args()

    print("== gVisor sandbox verification ==")
    errors: list[str] = []
    print("-- repo assets --")
    errors.extend(check_repo_assets())
    print("-- settings defaults --")
    if not args.live:
        errors.extend(check_settings_defaults())
    else:
        print("  SKIP settings defaults (--live runs on production gVisor hosts)")
    print("-- OCI config --")
    errors.extend(check_oci_config_shape())

    if args.live:
        print("-- live runsc --")
        errors.extend(asyncio.run(check_live_runsc()))
        print("-- live desk --")
        errors.extend(asyncio.run(check_live_desk_health()))
    else:
        print("-- live runsc -- (skipped; pass --live on Linux/Docker)")
        print("-- live desk -- (skipped; pass --live on Linux/Docker)")

    if errors:
        print(f"\nFAILED ({len(errors)} issue(s))")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
