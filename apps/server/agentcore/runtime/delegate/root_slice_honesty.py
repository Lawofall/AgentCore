"""根委派切片诚实软闸：单节点手写写工程且无结构钉 → 软告警.

产品判据（可证明结构，不扫用户/task 长文意图）：
根 depth=0、无具名 playbook、本批恰好 1 task、显式写工程
（只认 ``form=workspace``；``form=files`` / 省略不算）、且无切片钉 →
记一次软告警。不拒收、不改图。路径 B（该 lead 嵌套扇出）为合法等价编制，文案须明示。

切片钉（白名单，任一即豁免）：非空 ``artifacts`` / 非空 ``artifact_dir`` /
非空 ``required_sections`` / 本 task ``checkpoint_after``。
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.runs.playbooks import PLAYBOOKS


def _deliverable(task: dict[str, Any]) -> dict[str, Any]:
    raw = task.get("deliverable")
    return raw if isinstance(raw, dict) else {}


def _explicit_write_engineering(task: dict[str, Any]) -> bool:
    """True only when CEO explicitly marked ``form=workspace`` (files / omit ≠ 写工程)."""
    return _deliverable(task).get("form") == "workspace"


def _has_slice_nail(task: dict[str, Any]) -> bool:
    """Structural slice boundary — whitelist only, no free-text heuristics."""
    if task.get("checkpoint_after"):
        return True
    d = _deliverable(task)
    arts = d.get("artifacts")
    if isinstance(arts, list) and any(isinstance(a, str) and a.strip() for a in arts):
        return True
    artifact_dir = d.get("artifact_dir")
    if isinstance(artifact_dir, str) and artifact_dir.strip():
        return True
    sections = d.get("required_sections")
    return isinstance(sections, list) and any(
        isinstance(s, str) and s.strip() for s in sections
    )


def root_slice_honesty_soft_message() -> str:
    return (
        "首批单节点手写写工程，且未用结构钉本轮切片——"
        "请用根侧多节点 / 具名 playbook / deliverable 钉边界"
        "（artifacts、artifact_dir、required_sections 等），"
        "或由该 lead 嵌套扇出并写清子任务验收；"
        "本提示不拒收、不改图。"
    )


def check_root_slice_honesty(
    tasks: list[Any],
    *,
    depth: int = 0,
    playbook: str | None = None,
) -> str | None:
    """根侧无边界整锅风险时返回软告警文案；否则 None."""
    if depth != 0:
        return None
    if isinstance(playbook, str) and playbook.strip() in PLAYBOOKS:
        return None
    if not isinstance(tasks, list) or not tasks:
        return None

    dict_tasks = [t for t in tasks if isinstance(t, dict)]
    if len(dict_tasks) != 1:
        return None

    task = dict_tasks[0]
    if not _explicit_write_engineering(task):
        return None
    if _has_slice_nail(task):
        return None

    return root_slice_honesty_soft_message()
