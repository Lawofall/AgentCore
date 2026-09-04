"""部署链必须在停-api 窗口内跑盘上迁移，并且先把盘备好。

两个脚本都曾**完全不调** ``scripts/migrate_workspace_tree.py``（全仓无调用点），后果不是
「迁移晚一点」而是永久丢文件：``resolve_workspace_root`` 无条件 ``mkdir``，新 api 一接
流量，第一个打开云文件夹的用户就把搬迁目标建成空目录；搬迁「目标已存在就跳过、绝不合并」，
运维事后补跑一律被判 skipped，文件永远停在旧的平铺目录里。

所以这里钉的是**顺序**，不只是「有没有调」：alembic 回填 rel_path 在前，tree 搬迁居中，
读 ``tree/<rel_path>/`` 的 project-docs 在后，全部在起 api 之前。

另两件同源的事也钉在这里：

* 那次搬迁**单向不可逆**（无反向脚本），而部署前快照只 ``pg_dump`` 了库。所以盘上
  ``workspaces/`` 必须先备份、备不成就不许往下走；且备份要排在**停 api 之前**，失败时
  只是取消这次部署，而不是把「保护数据的闸」变成新的停机源。
* 迁移脚本用非零退出码表达的不都是致命错误（tree 的 2 = 已安全跳过待人工确认，docs 的
  3 = 一个工作区目录都没扫到的保险）。``set -e`` 一视同仁会把这种「什么都没做」变成
  停机，而且稳定复现——之后每次部署都卡在同一步。
"""

import re
from pathlib import Path

import pytest

_DEPLOY_SCRIPTS = Path(__file__).resolve().parents[3] / "deploy" / "scripts"

# 备份 workspaces/ 的那一步（两个脚本用同一条 tar 命令，只有 compose 调用姿势不同）。
_WORKSPACE_ARCHIVE = "tar -czf - -C /data workspaces"


def _command_lines(path: Path) -> list[str]:
    """只留会执行的行——顺序断言不能被文件头那段流程说明注释带偏。"""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def _logical_lines(path: Path) -> list[str]:
    """把反斜杠续行拼回一条命令——断言「这一步是怎么调起来的」不能被换行切开。"""
    joined: list[str] = []
    pending = ""
    for line in _command_lines(path):
        stripped = line.strip()
        if stripped.endswith("\\"):
            pending += stripped[:-1].strip() + " "
            continue
        joined.append((pending + stripped).strip())
        pending = ""
    if pending:
        joined.append(pending.strip())
    return joined


def _first_containing(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise AssertionError(f"部署脚本里找不到 {needle!r}")


def _first_exact(lines: list[str], text: str) -> int:
    for i, line in enumerate(lines):
        if line.strip() == text:
            return i
    raise AssertionError(f"部署脚本里找不到整行 {text!r}")


# 同一流程的两个入口；停 / 起 api 的写法各自不同，故随文件名一起带上。
# 停机必须带上 sandboxd：guest 占工作区盘，只停 api 迁树会和 bind 打架。
_SCRIPTS = [
    (
        "finish-server.sh",
        '"${COMPOSE[@]}" stop --timeout 40 api sandboxd 2>/dev/null || true',
        '"${COMPOSE[@]}" up -d',
    ),
    (
        "deploy-server.sh",
        "dc stop --timeout 40 api sandboxd 2>/dev/null || true",
        "dc up -d",
    ),
]


@pytest.mark.parametrize(("filename", "stop_api", "start_api"), _SCRIPTS)
def test_disk_migrations_run_between_alembic_and_starting_the_api(
    filename: str, stop_api: str, start_api: str
):
    lines = _command_lines(_DEPLOY_SCRIPTS / filename)

    stopped = _first_exact(lines, stop_api)
    alembic = _first_containing(lines, "alembic upgrade head")
    tree = _first_containing(lines, "scripts/migrate_workspace_tree.py")
    docs = _first_containing(lines, "scripts/migrate_project_docs.py")
    started = _first_exact(lines, start_api)

    assert stopped < alembic < tree < docs < started


@pytest.mark.parametrize(("filename", "stop_api", "start_api"), _SCRIPTS)
def test_workspace_backup_runs_before_the_api_stops(
    filename: str, stop_api: str, start_api: str
):
    """盘上备份必须整个排在停 api 之前，量体积和查空间也一样。

    排在停 api **之后**就等于拿停机换备份：备份一失败，api 已经躺下了。排在前面，失败
    的代价只是取消这次部署——旧 api 照常服务，盘上数据一个字节没动。

    体积探测与空间检查同样得在写档之前：``workspaces/`` 是全体用户的文件，写到一半撑爆
    磁盘会连备份带在跑的 api 一起毁掉，所以空间不够要在动手前就硬停。
    """
    lines = _command_lines(_DEPLOY_SCRIPTS / filename)

    sized = _first_containing(lines, "du -sk /data/workspaces")
    space = _first_containing(lines, "df -Pk")
    archived = _first_containing(lines, _WORKSPACE_ARCHIVE)
    stopped = _first_exact(lines, stop_api)

    assert sized < space < archived < stopped
    assert any(line.strip() == "exit 1" for line in lines[space:archived]), (
        "空间不足必须在写档之前硬停，不能写到一半才发现"
    )


@pytest.mark.parametrize(("filename", "stop_api", "start_api"), _SCRIPTS)
def test_unverified_workspace_backup_aborts_the_deploy(
    filename: str, stop_api: str, start_api: str
):
    """备份没成功就不许往下走——「以为备了」和没备一样致命。

    单向搬迁跑完就没有回头路，所以这里不接受「tar 退了 0 就算备好」：先写 ``.partial``、
    ``gzip -t`` 过了才改成正式名，任何一条失败路径都要在停 api 之前 ``exit``。
    """
    lines = _command_lines(_DEPLOY_SCRIPTS / filename)

    archived = _first_containing(lines, _WORKSPACE_ARCHIVE)
    stopped = _first_exact(lines, stop_api)
    window = lines[archived:stopped]

    assert "|| true" not in lines[archived], "tar 的失败不能被 `|| true` 抹平"

    verified = next(i for i, line in enumerate(window) if "gzip -t" in line)
    promoted = next(i for i, line in enumerate(window) if line.strip().startswith("mv "))
    assert verified < promoted, "先校验再改名：半截归档不许冒充「有备份」"

    assert any(line.strip() == "exit 1" for line in window[:verified]), (
        "tar 真出错（非「文件在读时被改」）必须终止部署"
    )
    assert any(line.strip() == "exit 1" for line in window[verified:promoted]), (
        "归档损坏或为空必须终止部署"
    )


@pytest.mark.parametrize(("filename", "stop_api", "start_api"), _SCRIPTS)
def test_no_op_migration_exit_codes_do_not_strand_the_api(
    filename: str, stop_api: str, start_api: str
):
    """「安全地什么都没做」的退出码不该造成停机，但真失败仍须硬停。

    ``migrate_workspace_tree.py`` 的 2 是「目标目录已存在、已跳过、请人工确认」，
    ``migrate_project_docs.py`` 的 3 是「一个工作区目录都没扫到」的保险。两步都排在起 api
    之前，``set -e`` 一视同仁就会让这种无操作态把 api 停在地上；更糟的是它稳定复现，之后
    每次部署都卡在同一步。

    所以放行必须**点名退出码**：``|| true`` 那种一锅端会连真正的迁移失败一起吞掉，新 api
    就带着半截盘上布局接流量了。换别的做法（比如失败路径也把 api 起回来）请连同本用例一起
    改写——要钉住的是「无操作不停机 且 真失败不放过」这条性质。
    """
    lines = _logical_lines(_DEPLOY_SCRIPTS / filename)

    tree = lines[_first_containing(lines, "scripts/migrate_workspace_tree.py")]
    docs = lines[_first_containing(lines, "scripts/migrate_project_docs.py")]

    assert re.match(r'^migrate_step "[^"]+" 2\b', tree), tree
    assert re.match(r'^migrate_step "[^"]+" 3\b', docs), docs
    assert "|| true" not in tree
    assert "|| true" not in docs


@pytest.mark.parametrize(("filename", "stop_api", "start_api"), _SCRIPTS)
def test_deploy_scripts_use_unix_newlines(filename: str, stop_api: str, start_api: str):
    """Linux 上 bash 会把 CRLF 的 ``\\r`` 吃进命令名，部署脚本必须是 LF。"""
    raw = (_DEPLOY_SCRIPTS / filename).read_bytes()
    assert b"\r\n" not in raw


def test_api_stop_grace_exceeds_salvage_window():
    """Compose + deploy stop paths stay at 40s; in-process budget must fit inside.

    Docker's default 10s SIGKILLs mid-salvage and orphans leases. The 40s grace is
    not lengthened: uvicorn drain + salvage + teardown hard cap + slack ≤ 40.
    Prod must not use uvicorn ``timeout_graceful_shutdown=None`` (SSE drain never
    reaches lifespan).
    """
    from agentcore.config.checkpoint import CheckpointSettings
    from agentcore.config.persistence import PersistenceSettings
    from agentcore.config.server import ServerSettings
    from agentcore.config.workspace import WorkspaceSettings

    docker_grace = 40.0
    drain = float(ServerSettings.model_fields["uvicorn_graceful_shutdown_seconds"].default)
    salvage = float(CheckpointSettings.model_fields["turn_shutdown_grace_seconds"].default)
    teardown = float(ServerSettings.model_fields["shutdown_teardown_seconds"].default)
    close_all = float(
        WorkspaceSettings.model_fields["browser_shutdown_close_all_seconds"].default
    )
    compaction = float(PersistenceSettings.model_fields["compaction_shutdown_seconds"].default)
    slack = docker_grace - drain - salvage - teardown
    assert drain == 5.0
    assert salvage == 20.0
    assert teardown == 8.0
    assert slack == 7.0
    assert drain + salvage + teardown + slack <= docker_grace
    assert close_all + compaction <= teardown

    app_yml = _DEPLOY_SCRIPTS.parent / "docker-compose.app.yml"
    assert "stop_grace_period: 40s" in app_yml.read_text(encoding="utf-8")
    for name in ("deploy-server.sh", "finish-server.sh", "restore.sh"):
        text = (_DEPLOY_SCRIPTS / name).read_text(encoding="utf-8")
        assert "stop --timeout 40 api" in text

    main_py = Path(__file__).resolve().parents[1] / "agentcore" / "__main__.py"
    main_text = main_py.read_text(encoding="utf-8")
    assert "timeout_graceful_shutdown=2 if reload else None" not in main_text
    assert "uvicorn_graceful_shutdown_seconds" in main_text


def test_rollback_does_not_repeat_the_workspace_backup():
    """回滚不跑正向盘上迁移，也就不该再备一份。finish-server.sh 没有回滚分支。"""
    lines = _command_lines(_DEPLOY_SCRIPTS / "deploy-server.sh")
    guard = _first_containing(
        lines, '$IS_ROLLBACK" -eq 0 && "${SKIP_WORKSPACE_SNAPSHOT:-0}" != "1"'
    )
    archived = _first_containing(lines, _WORKSPACE_ARCHIVE)
    stopped = _first_exact(lines, "dc stop --timeout 40 api sandboxd 2>/dev/null || true")
    assert guard < archived < stopped
    assert "${SKIP_WORKSPACE_SNAPSHOT:-0}" in lines[guard]
