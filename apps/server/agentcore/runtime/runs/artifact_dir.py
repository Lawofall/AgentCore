"""约定文档 ``artifact_dir``：布局常量 → 委派交付默认目录 + 验收前缀。

工作区布局事实见 ``workspace_context``；本模块只在 ``form=files`` /
已声明 ``artifacts`` 时按 ``stage_dirs`` 填默认落盘目录。Worker 只定文件名。

**落点只认显式来源**（按序）：``deliverable.workspace_native``（真 = 产物是用户
工作区原生文件，无约定落点）→ 已声明 ``artifacts`` 推导出的目录 → 显式
``deliverable.artifact_dir`` → 默认 ``DRAFTS_DIR``（``AgentCore/文档/工作稿``）。
``workspace_native`` 与 leftover ``artifact_dir`` 在 ``apply_artifact_dir_defaults``
互斥：已定位路径旁的 leftover 目录清掉（native 压过误钉的工作间路径）；裸文件名
+ 显式目录则 join 成全路径并关掉 native（目录是路径合同，避免任务书两句打架）。
运行时**不**扫 role / task 自由文猜「像调研还是像审查」——那条整链已净删除
（意图分类器形态，且误判对用户不可见）。``research/`` 有机器语义（辩手读它
取证），只经 playbook 常量或显式声明进入。→ 双模式工作区 §四

**验收 vs 归属分键**：``artifact_dir`` / 目录前缀 / 通配 = 验收覆盖；具体文件
路径 = C3 归属与 sibling 互斥。裸目录**永不**注入 ``artifacts`` 冒充归属键。

**与声明产物对齐**：非空 ``artifacts`` 若已落在 ``AgentCore/文档/…``（含自定义
子目录如 ``AI开发/``，不限于约定 stage 目录），案卷核对目录由这些路径推导；
业务向 ``artifacts``（``src/`` · ``site/`` 等）自带落点，不套约定文档目录。

不做：``file_write`` 启发式改写、根目录搬迁、省略 playbook 手写特例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentcore.workspace.stage_dirs import (
    DEBATE_DIR,
    DOCS_PREFIX,
    DRAFTS_DIR,
    RESEARCH_DIR,
    REVIEWS_DIR,
)

if TYPE_CHECKING:
    from agentcore.runtime.runs.types import Deliverable, RunSpec

_STAGE_DIRS = (DRAFTS_DIR, RESEARCH_DIR, DEBATE_DIR, REVIEWS_DIR)


def normalize_artifact_dir(path: str) -> str:
    """Workspace-relative POSIX dir without trailing slash."""
    return path.replace("\\", "/").strip().lstrip("./").rstrip("/")


def stage_dir_covering(path: str) -> str:
    """Return the stage dir that covers ``path``, or ``\"\"``."""
    p = normalize_artifact_dir(path)
    if not p:
        return ""
    for d in _STAGE_DIRS:
        if p == d or p.startswith(f"{d}/"):
            return d
    return ""


def _looks_like_business_artifact(path: str) -> bool:
    """True when path has a non-dossier directory structure (e.g. ``site/index.html``)."""
    p = normalize_artifact_dir(path)
    if not p or "/" not in p:
        return False
    return not (p == DOCS_PREFIX or p.startswith(f"{DOCS_PREFIX}/"))


def _acceptance_dir_for_docs_path(path: str) -> str:
    """Map one artifact pattern under ``AgentCore/文档/`` to an acceptance dir.

    Stage dirs collapse to the stage root; custom subtrees (e.g. ``AI开发/``) keep
    the concrete parent directory so contract checks match declared products.
    """
    raw = path.replace("\\", "/").strip()
    if not raw:
        return ""
    covered = stage_dir_covering(raw)
    if covered:
        return covered

    # Glob: take the directory prefix before the first wildcard.
    if any(ch in raw for ch in "*?["):
        cut = len(raw)
        for ch in "*?[":
            idx = raw.find(ch)
            if idx != -1:
                cut = min(cut, idx)
        raw = raw[:cut].rstrip("/")
        if not raw:
            return ""
        covered = stage_dir_covering(raw)
        if covered:
            return covered

    ended_as_dir = raw.endswith("/")
    p = normalize_artifact_dir(raw)
    if not p or not (p == DOCS_PREFIX or p.startswith(f"{DOCS_PREFIX}/")):
        return ""
    if p == DOCS_PREFIX:
        return DOCS_PREFIX

    rel = p[len(DOCS_PREFIX) + 1 :]
    if "/" not in rel:
        # Single segment under 文档/：无扩展名或原带尾斜杠 → 子目录；否则视为文件。
        if ended_as_dir or "." not in rel:
            return p
        return DOCS_PREFIX
    return p.rsplit("/", 1)[0]


def _dir_from_artifacts(artifacts: list[str]) -> str:
    """Common acceptance dir derived from declared docs artifacts, or ``\"\"``."""
    dirs: list[str] = []
    for raw in artifacts:
        if not isinstance(raw, str):
            continue
        d = _acceptance_dir_for_docs_path(raw)
        if d:
            dirs.append(d)
    if not dirs:
        return ""
    common = dirs[0].split("/")
    for d in dirs[1:]:
        parts = d.split("/")
        n = 0
        while n < len(common) and n < len(parts) and common[n] == parts[n]:
            n += 1
        common = common[:n]
        if len(common) < 2:
            return ""
    result = "/".join(common)
    if result != DOCS_PREFIX and not result.startswith(f"{DOCS_PREFIX}/"):
        return ""
    return stage_dir_covering(result) or result


def resolve_artifact_dir(deliverable: Deliverable) -> str:
    """Resolve the dossier dir for a file deliverable, or ``\"\"`` when not applicable.

    Explicit sources only — the deliverable itself. Role / task free text is
    **not** an input: no signature to read it from, so the deleted intent
    classifier cannot creep back in.
    """
    # 最高优先级：产物是用户工作区原生文件（源码 / 项目文件）→ 无约定落点。
    # 压过 ``artifacts`` 推导与显式 ``artifact_dir``：写码节点即便声明了工作间
    # 路径，代码也该留在工作区里它本来的位置。
    if deliverable.workspace_native:
        return ""
    if deliverable.form == "prose":
        return ""
    fileish = deliverable.form == "files" or bool(deliverable.artifacts)
    if not fileish:
        return ""

    # Declared product paths win over a mismatched/default ``artifact_dir``
    # (e.g. writer artifacts under ``文档/AI开发`` must not keep ``research``).
    derived = _dir_from_artifacts(list(deliverable.artifacts or []))
    if derived:
        return derived

    explicit = normalize_artifact_dir(deliverable.artifact_dir)
    if explicit:
        return explicit

    if any(_looks_like_business_artifact(a) for a in deliverable.artifacts):
        return ""

    return DRAFTS_DIR


def is_acceptance_only_artifact_pattern(path: str) -> bool:
    """True for directory / glob patterns that must not become C3 ownership keys."""
    raw = path.replace("\\", "/").strip()
    if not raw:
        return True
    if raw.endswith("/") or any(ch in raw for ch in "*?["):
        return True
    p = normalize_artifact_dir(raw)
    if not p:
        return True
    # Exact stage dir (``AgentCore/文档/research``) — shared dossier namespace.
    return stage_dir_covering(p) == p


def is_file_ownership_path(path: str) -> bool:
    """Concrete file path eligible for sibling / ownership declare."""
    return not is_acceptance_only_artifact_pattern(path)


def _has_relocatable_bare_filename(artifacts: list[str] | None) -> bool:
    """True when a concrete filename has no directory and can join under ``artifact_dir``."""
    for raw in artifacts or []:
        if not isinstance(raw, str):
            continue
        raw_s = raw.replace("\\", "/").strip()
        if not raw_s or is_acceptance_only_artifact_pattern(raw_s):
            continue
        norm = normalize_artifact_dir(raw_s)
        if norm and "/" not in norm:
            return True
    return False


def apply_artifact_dir_defaults(deliverable: Deliverable) -> None:
    """Fill ``artifact_dir``; relocate bare filenames under it (in-place).

    Empty ``artifacts`` stays empty — acceptance uses ``artifact_dir`` directly;
    do not inject ``[dir/]`` (that falsely exclusivizes a shared dossier).

    ``workspace_native`` and an explicit ``artifact_dir`` never both survive:
    leftover dir next to already-located paths is dropped (native outranks a
    mistaken dossier pin); leftover dir + bare filenames is a path contract —
    native is cleared so the join runs and task briefs stay consistent.
    """
    leftover = normalize_artifact_dir(deliverable.artifact_dir)
    if deliverable.workspace_native:
        if leftover and _has_relocatable_bare_filename(deliverable.artifacts):
            deliverable.workspace_native = False
        else:
            deliverable.artifact_dir = ""
            return

    resolved = resolve_artifact_dir(deliverable)
    if not resolved:
        return

    deliverable.artifact_dir = resolved

    if not deliverable.artifacts:
        return

    relocated: list[str] = []
    for raw in deliverable.artifacts:
        if not isinstance(raw, str):
            continue
        raw_s = raw.replace("\\", "/").strip()
        if not raw_s:
            continue
        if is_acceptance_only_artifact_pattern(raw_s):
            if any(ch in raw_s for ch in "*?["):
                relocated.append(normalize_artifact_dir(raw_s) or raw_s)
            else:
                bare = normalize_artifact_dir(raw_s)
                if bare:
                    relocated.append(f"{bare}/")
            continue
        norm = normalize_artifact_dir(raw_s)
        if not norm:
            continue
        if "/" not in norm:
            relocated.append(f"{resolved}/{norm}")
        else:
            relocated.append(norm)
    deliverable.artifacts = relocated


def apply_artifact_dir_to_spec(spec: RunSpec) -> None:
    """Apply dossier ``artifact_dir`` defaults to one plan node (in-place)."""
    if spec.deliverable is None:
        return
    apply_artifact_dir_defaults(spec.deliverable)


def apply_artifact_dir_to_specs(specs: list[RunSpec]) -> None:
    for spec in specs:
        apply_artifact_dir_to_spec(spec)


def apply_artifact_dir_to_plan(plan: object) -> None:
    nodes = getattr(plan, "nodes", None) or []
    apply_artifact_dir_to_specs(list(nodes))


__all__ = [
    "apply_artifact_dir_defaults",
    "apply_artifact_dir_to_plan",
    "apply_artifact_dir_to_spec",
    "apply_artifact_dir_to_specs",
    "is_acceptance_only_artifact_pattern",
    "is_file_ownership_path",
    "normalize_artifact_dir",
    "resolve_artifact_dir",
    "stage_dir_covering",
]
