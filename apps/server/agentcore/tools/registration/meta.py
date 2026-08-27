"""Registration metadata types and helpers (no tool-class imports)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast

from agentcore.tools.protocol import Tool, ToolSchema

# Audience tokens — same strings as ``tools.catalog.AVAILABLE_TO_*``.
AUDIENCE_CEO = "ceo"
AUDIENCE_WORKER = "worker"
AUDIENCE_CEO_ONLY: tuple[str, ...] = (AUDIENCE_CEO,)
AUDIENCE_WORKER_ONLY: tuple[str, ...] = (AUDIENCE_WORKER,)
AUDIENCE_BOTH: tuple[str, ...] = (AUDIENCE_CEO, AUDIENCE_WORKER)


class ToolSurface(StrEnum):
    """Where a tool class is collected into a runtime registry / catalog section."""

    BUILTIN = "builtin"
    WORKER_ONLY = "worker_only"
    CEO_ORCHESTRATION = "ceo_orchestration"


class FileProductsContract(StrEnum):
    """本工具与「交付物台账」的关系——落盘产物自报契约。**开发期棘轮判据，运行时永不读**。

    台账（``files_touched`` / ``file_acceptance``）的事实口径是**工具自报**：落盘的工具在
    ``ToolResult.file_products`` 上声明它真正写出的路径（契约见
    ``agentcore.tools.file_products``）。旧口径是引擎按工具名白名单猜谁产了文件——
    ``md_to_docx`` 从注册那天起就没进过任何一份名单，四份副本的对齐测试全绿、线上静默漏账
    数月，直到用户看见 AI 报出错误的文件路径才暴露。**致命的不是名单少了谁，是默认不安全**：
    漏登记 = 静默通过。

    翻转成自报后，同一形状的事故会变成「新工具忘了填 ``file_products``」。所以「落盘面」上
    （FILESYSTEM 类 ∪ ``execution_class``）的每个工具都必须在这里**显式**表态属于哪一类，
    漏声明就停在 :attr:`UNDECLARED` 上、被 ``tests/test_file_products_ratchet.py`` 判红。
    红了怎么办：那个测试的模块 docstring 逐类写了修法。
    """

    # 未声明——只对碰不到工作区落盘的工具（编排 / 检索 / 交互 …）合法。落盘面上出现它 = 红。
    UNDECLARED = "undeclared"
    # 会落盘，且在 ``ToolResult.file_products`` 自报真实落盘路径（非模型请求的原始路径）。
    # 棘轮要求它在用例表里有一条**真跑**用例钉住自报内容——只声明不实现照样红。
    SELF_REPORT = "self_report"
    # 迁移中：会落盘、自报还没接。待接清单**只减不增**（棘轮用真跑用例证明它确实还没自报），
    # 现已清空——再用这一档要先推翻棘轮里那条「下界是空」的断言。
    SELF_REPORT_PENDING = "self_report_pending"
    # 不往工作区写任何字节（file_read / file_list / glob / grep / code_search / code_diagnostics）。
    # 与审批面互锁：FILESYSTEM 类里只有 ``ToolApproval.NEVER`` 才配声明只读——要写盘授权
    # 又自称只读的组合会被棘轮拦下。
    READ_ONLY = "read_only"
    # 会动工作区，但落的不是台账要记的产物：只建目录 / 只删文件 / 浏览器关键帧 /
    # 在沙箱或用户机器上跑进程留下的副产物（枚举不出、也不是本回合交付物）/ ``git`` 换工作树
    # （checkout / pull / merge 落下的是别人或过去已提交的版本，不是本 run 的产出）。
    NO_PRODUCT = "no_product"


class CeoWire(StrEnum):
    """When a CEO-orchestration tool is wired at runtime (catalog always lists it)."""

    ALWAYS = "always"
    MEMORY = "memory"
    # Unified on-demand catalog non-empty → ``consult`` (技能 ∪ 规则 ∪ 记忆主题).
    CONSULT = "consult"
    CHECKPOINT = "checkpoint"
    BOARD = "board"
    # Advertised in catalog; runtime inject via ``ceo_surface`` (idle/coord gate).
    COORDINATION = "coordination"


@dataclass(frozen=True)
class ToolRegistration:
    """Class-level registration metadata collected by registries / catalog / wire."""

    surface: ToolSurface
    audience: tuple[str, ...]
    execution_class: bool = False
    local_only: bool = False
    ceo_wire: CeoWire = CeoWire.ALWAYS
    # ``code_execute`` / ``terminal`` stamp description from backend location.
    needs_location: bool = False
    # Probe-trimmed ``code_execute`` language enum. Factory forwards ``languages=``
    # only when this is True — ``needs_location`` alone must not imply the kwarg
    # (``terminal`` takes location only).
    accepts_exec_languages: bool = False
    # L3 team browser (D11 / C1): gated by ``browser_execution_enabled_for`` ON TOP OF
    # ``execution_class`` — server+gVisor, local+Bridge, **or** local 过桥无 Bridge
    # but gVisor/netns healthy (host_kind=sandbox; never open_local_bridge_session).
    browser_class: bool = False
    # Host 第三能力面: gated by ``host≠off`` + desktop backfill channel (desktop_online).
    # Must NOT set ``execution_class`` — L2/L3 never enter kickoff silent grant.
    host_class: bool = False
    # Desktop-online-only tools (≠ Host face): gated solely by ``desktop_online``
    # (e.g. ``external_mount_readonly``). Not gated by ``host≠off``.
    desktop_online_class: bool = False
    # Workspace-git face (``git``): gated by ``git_execution_enabled_for`` — a root
    # to spawn ``git`` under (cloud / sidecar), else a live desktop channel.
    git_class: bool = False
    # Catalog-gated tools: listed on the roster + capability catalog, but NOT
    # auto-registered by ``build_worker_registry``. Callers wire them after the
    # registry is built (e.g. ``_wire_worker_conversation_log_tools``). Same
    # pattern as ``consult_memory``.
    manual_wire: bool = False
    # 落盘产物自报契约（见 :class:`FileProductsContract`）。**只有开发期棘轮读它**——
    # 引擎 / 台账一律读 ``ToolResult.file_products``，绝不按这个字段（更不按工具名）判谁产了
    # 文件。落盘面上留着默认值 = 棘轮红灯。
    file_products: FileProductsContract = FileProductsContract.UNDECLARED
    # 本工具能落地的目标后缀（带点，如 ``.docx``）。空 = 不是格式生产者，不进
    # ``<workspace_context>`` 产物格式行。专用导出器写自己的输出；``execution_class``
    # 工具只写「没有专用导出器、靠脚本才能产」的格式。事实行读本字段 + 本回合装配闸，
    # 不另维护一份格式白名单。
    produces_formats: tuple[str, ...] = ()


def tool_registration(cls: type) -> ToolRegistration:
    reg = getattr(cls, "registration", None)
    if not isinstance(reg, ToolRegistration):
        raise TypeError(f"{cls.__name__} must declare class attribute ``registration``")
    return reg


def read_static_schema(tool_cls: type) -> ToolSchema:
    """Read a pure-static ``schema`` without running heavy ``__init__``."""
    instance: Tool = cast(Tool, object.__new__(tool_cls))
    return instance.schema


def declared_tool_schema(cls: type) -> ToolSchema:
    """Schema of a declared class without runtime wiring (location-aware)."""
    reg = tool_registration(cls)
    if reg.needs_location:
        return cls(location=None).schema  # type: ignore[call-arg]
    if reg.surface is ToolSurface.CEO_ORCHESTRATION:
        return read_static_schema(cls)
    return cls().schema  # type: ignore[call-arg]


def declared_tool_name(cls: type) -> str:
    return declared_tool_schema(cls).name


def instantiate_declared(
    cls: type,
    *,
    location: Literal["server", "local"] | None = None,
    languages: tuple[str, ...] | list[str] | None = None,
) -> Any:
    """Zero-arg (or location-aware) construction for builtin / worker-only / board /
    ALWAYS tools."""
    reg = tool_registration(cls)
    kwargs: dict[str, Any] = {}
    if reg.needs_location:
        kwargs["location"] = location
    if languages is not None and reg.accepts_exec_languages:
        kwargs["languages"] = languages
    return cls(**kwargs)
