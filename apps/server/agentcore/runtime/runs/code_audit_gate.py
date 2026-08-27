"""``code_audit`` 报告结构闸（L2b）：校验 ``*.audit.json`` 字段语义。

与成篇审计硬门（``cite_write_review`` 独立审校）正交；与协议 ``ProjectedTurn`` 无关。
由 :class:`~agentcore.runtime.runs.types.Deliverable` 的 ``code_audit_gate`` 盖戳触发，
挂在 :func:`~agentcore.runtime.runs.contract.check_contract`。

写盘通道不可用（``landing_failure_kind=channel_dead|write_failed``）时：缺/读不到
配套 JSON 的**缺产物**失败不硬拒（归因走零写 soft tip）；已读到的 JSON 仍做字段语义校验。

Markdown 报告已落盘、仅缺配套 ``*.audit.json`` 时：同样走缺产物降级（部分交付 /
可补写修复），不判整节点 ``contract.failed``；已读到的 JSON 仍做字段语义校验。
"""

from __future__ import annotations

import json
import re
from typing import Any

_VERIFICATIONS = frozenset({"全文精读", "运行验证", "静态推断·未读全", "待核实"})
_VERDICTS = frozenset({"属实", "误报", "部分属实", "待核实"})
_SEVERITIES = frozenset({"高", "中", "低", "观察·工程"})
_SECURITY_CATEGORIES = frozenset({"安全", "路径", "注入"})

# 用户脸 / 契约分脸：本闸失败文案统一此前缀（禁前端扫正文猜脸；后端按前缀归 ``format``）。
STRUCTURE_FAILURE_PREFIX = "结构闸："

# 常见英文 / P 级同义 → 中文闭集（精确匹配，大小写不敏感；禁复合怪写 / 万能清洗）。
# P0–P3 钉死：P0→高，P1→中，P2→低，P3→观察·工程（与 critical/high/info 惯例对齐）。
_SEVERITY_SYNONYMS: dict[str, str] = {
    "high": "高",
    "critical": "高",
    "p0": "高",
    "medium": "中",
    "med": "中",
    "moderate": "中",
    "p1": "中",
    "low": "低",
    "p2": "低",
    "info": "观察·工程",
    "informational": "观察·工程",
    "observation": "观察·工程",
    "observational": "观察·工程",
    "notice": "观察·工程",
    "engineering": "观察·工程",
    "p3": "观察·工程",
}
_VERDICT_SYNONYMS: dict[str, str] = {
    "confirmed": "属实",
    "true": "属实",
    "valid": "属实",
    "real": "属实",
    "false_positive": "误报",
    "false-positive": "误报",
    "false positive": "误报",
    "fp": "误报",
    "invalid": "误报",
    "partial": "部分属实",
    "partially_confirmed": "部分属实",
    "partially-confirmed": "部分属实",
    "partially confirmed": "部分属实",
    "partially_true": "部分属实",
    "partially true": "部分属实",
    "pending": "待核实",
    "unverified": "待核实",
    "needs_verification": "待核实",
    "needs-verification": "待核实",
    "to_verify": "待核实",
    "to-verify": "待核实",
}

# L3：全量 typecheck / pytest 超时不得充当中+缺陷证据。
_TIMEOUT_AS_DEFECT = re.compile(
    r"(typecheck|tsc\b|pytest|test:server:unit).{0,40}(timeout|超时|预算耗尽|exceeded\s+\d+s)"
    r"|(timeout|超时|预算耗尽).{0,40}(typecheck|tsc\b|pytest|全量单测)",
    re.IGNORECASE,
)


def _normalize_closed(value: str, *, chinese: frozenset[str], synonyms: dict[str, str]) -> str:
    """Map Chinese closed-set or common English synonym; otherwise return stripped raw."""
    raw = value.strip()
    if not raw:
        return ""
    if raw in chinese:
        return raw
    return synonyms.get(raw.lower(), raw)


def normalize_audit_severity(value: str) -> str:
    """Normalize severity input to Chinese authority (or stripped raw if unknown)."""
    return _normalize_closed(
        value, chinese=_SEVERITIES, synonyms=_SEVERITY_SYNONYMS
    )


def normalize_audit_verdict(value: str) -> str:
    """Normalize verdict input to Chinese authority (or stripped raw if unknown)."""
    return _normalize_closed(value, chinese=_VERDICTS, synonyms=_VERDICT_SYNONYMS)


def is_code_audit_structure_failure(message: str) -> bool:
    """True when ``message`` was stamped by this gate (structure-face classifier)."""
    return str(message or "").strip().startswith(STRUCTURE_FAILURE_PREFIX)


# 缺产物 / 读不到（非字段语义）。写盘不可用时由 check_contract 降硬，勿冒充「格式未过」。
_LANDING_ABSENCE_MARKERS = (
    "缺少 audit JSON 产物",
    "无法读取 audit JSON",
    "需要读取 audit JSON 文件内容",
    "未找到 *.audit.json 内容",
)


def is_code_audit_landing_absence_failure(message: str) -> bool:
    """True when the structure-face message is about missing/unreadable audit JSON."""
    text = str(message or "").strip()
    if not text.startswith(STRUCTURE_FAILURE_PREFIX):
        return False
    body = text[len(STRUCTURE_FAILURE_PREFIX) :]
    return any(m in body for m in _LANDING_ABSENCE_MARKERS)


def _path_matches_artifact(pattern: str, path: str) -> bool:
    nk = path.replace("\\", "/").strip()
    pat = pattern.replace("\\", "/").strip()
    if not nk or not pat:
        return False
    return nk == pat or nk.endswith("/" + pat) or nk.endswith(pat)


def code_audit_report_landed(
    *,
    artifacts: list[str],
    workspace_paths: list[str] | None,
    artifact_contents: dict[str, str] | None,
) -> bool:
    """True when a declared non-``*.audit.json`` artifact (typically Markdown) is on disk.

    Used by :func:`~agentcore.runtime.runs.contract.check_contract` to demote
    companion-JSON absence to partial delivery when the report body already landed.
    """
    report_pats = [
        p.replace("\\", "/").strip()
        for p in artifacts
        if isinstance(p, str)
        and p.replace("\\", "/").strip()
        and not p.replace("\\", "/").endswith(".audit.json")
    ]
    if not report_pats:
        return False
    contents = artifact_contents or {}
    paths = workspace_paths or []
    for pat in report_pats:
        for key, text in contents.items():
            if _path_matches_artifact(pat, key) and (text or "").strip():
                return True
        if any(_path_matches_artifact(pat, wp) for wp in paths if isinstance(wp, str)):
            return True
    return False


def _structure_fail(detail: str) -> str:
    return f"{STRUCTURE_FAILURE_PREFIX}{detail}"


def normalize_audit_evidence(value: Any) -> str:
    """Normalize evidence to a non-empty pointer string when possible.

    Accepts isomorphic shapes models commonly emit:
    - ``str`` (authority)
    - ``list[str]`` (joined with ``；``)
    - single-level ``{"path"|"file", "line"|"lines"|"start"}`` → ``path:line``

    Empty / unknown shapes → ``""`` (caller stamps structure failure).
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    parts.append(s)
            elif isinstance(item, dict):
                nested = normalize_audit_evidence(item)
                if nested:
                    parts.append(nested)
        return "；".join(parts)
    if isinstance(value, dict):
        path = _as_str(value.get("path") or value.get("file"))
        line = value.get("line")
        if line is None:
            line = value.get("lines")
        if line is None:
            line = value.get("start")
        if path and line is not None and isinstance(line, (str, int)):
            return f"{path}:{str(line).strip()}"
        if path:
            return path
        return ""
    return ""


def parse_audit_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(obj, error)``. ``error`` set when unparseable or not an object."""
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"audit JSON 无法解析：{exc}"
    if not isinstance(data, dict):
        return None, "audit JSON 根须为对象"
    return data, None


def validate_code_audit_payload(data: dict[str, Any]) -> list[str]:
    """Semantic failures for one ``*.audit.json`` object (empty = pass)."""
    failures: list[str] = []
    findings = data.get("findings")
    if not isinstance(findings, list):
        return [_structure_fail("audit JSON 缺少 findings 数组")]

    for i, item in enumerate(findings):
        prefix = f"findings[{i}]"
        if not isinstance(item, dict):
            failures.append(_structure_fail(f"{prefix} 须为对象"))
            continue
        sev = normalize_audit_severity(_as_str(item.get("severity")))
        ver = _as_str(item.get("verification"))
        verd = normalize_audit_verdict(_as_str(item.get("verdict")))
        evidence = normalize_audit_evidence(item.get("evidence"))
        summary = _as_str(item.get("summary") or item.get("id"))
        category = _as_str(item.get("category"))
        reach = _as_str(item.get("reachability"))
        trigger = _as_str(item.get("trigger_path"))

        if not summary:
            failures.append(_structure_fail(f"{prefix} 缺少 summary 或 id"))
        if sev not in _SEVERITIES:
            failures.append(
                _structure_fail(
                    f"{prefix} severity 无效（须为 高|中|低|观察·工程；"
                    "亦接受 P0–P3 / high|medium|low|info 等同义）"
                )
            )
        if ver not in _VERIFICATIONS:
            failures.append(_structure_fail(f"{prefix} verification 无效"))
        if verd not in _VERDICTS:
            failures.append(_structure_fail(f"{prefix} verdict 无效"))
        if not evidence:
            failures.append(
                _structure_fail(
                    f"{prefix} evidence 为空或无法归一"
                    "（须为非空 string，或非空 string[] / path+line 对象）"
                )
            )

        # 未读全 / 待核实 → 不得中+
        if (ver == "静态推断·未读全" or verd == "待核实") and sev in {"高", "中"}:
            failures.append(
                _structure_fail(
                    f"{prefix} 验证方式未读全或定案待核实时不得标中/高（现 severity={sev}）"
                )
            )

        # 高必须有触发路径
        if sev == "高" and not trigger:
            failures.append(_structure_fail(f"{prefix} severity=高 须写 trigger_path"))

        # 可达性：安全/路径/注入 或 高
        need_reach = sev == "高" or category in _SECURITY_CATEGORIES
        if need_reach and not reach:
            failures.append(
                _structure_fail(
                    f"{prefix} 安全/路径/注入类或 severity=高 须写 reachability"
                )
            )

        # L3：超时充中+
        if sev in {"高", "中"}:
            blob = f"{summary}\n{evidence}\n{_as_str(item.get('detail'))}"
            if _TIMEOUT_AS_DEFECT.search(blob):
                failures.append(
                    _structure_fail(
                        f"{prefix} 禁止把全量 typecheck/pytest 超时当作中+缺陷证据"
                        "（应标观察·工程）"
                    )
                )

    return failures


def code_audit_json_failures(
    *,
    artifacts: list[str],
    workspace_paths: list[str],
    artifact_contents: dict[str, str] | None,
) -> list[str]:
    """Locate ``*.audit.json`` among declared artifacts / contents and validate."""
    if artifact_contents is None:
        return [_structure_fail("code_audit 结构闸需要读取 audit JSON 文件内容")]

    declared = [
        p.replace("\\", "/").strip()
        for p in artifacts
        if isinstance(p, str) and p.replace("\\", "/").endswith(".audit.json")
    ]

    candidates: list[str] = []
    for pat in declared:
        matched_key: str | None = None
        for key in artifact_contents:
            nk = key.replace("\\", "/").strip()
            if nk == pat or nk.endswith("/" + pat) or nk.endswith(pat):
                matched_key = key
                break
        if matched_key is not None:
            candidates.append(matched_key)
            continue
        if any(
            p.replace("\\", "/").strip() == pat or p.replace("\\", "/").endswith("/" + pat)
            for p in workspace_paths
        ):
            return [_structure_fail(f"无法读取 audit JSON：`{pat}`")]
        return [_structure_fail(f"缺少 audit JSON 产物：`{pat}`")]

    if not candidates:
        candidates = [
            k for k in artifact_contents if k.replace("\\", "/").endswith(".audit.json")
        ]
    if not candidates and declared:
        return [_structure_fail(f"缺少 audit JSON 产物：`{declared[0]}`")]
    if not candidates:
        return [_structure_fail("code_audit 结构闸未找到 *.audit.json 内容")]

    failures: list[str] = []
    seen: set[str] = set()
    for key in candidates:
        nk = key.replace("\\", "/")
        if nk in seen:
            continue
        seen.add(nk)
        data, err = parse_audit_json(artifact_contents[key])
        if err:
            failures.append(_structure_fail(f"`{nk}` {err}"))
            continue
        assert data is not None
        failures.extend(validate_code_audit_payload(data))
    return failures


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "STRUCTURE_FAILURE_PREFIX",
    "code_audit_json_failures",
    "code_audit_report_landed",
    "is_code_audit_landing_absence_failure",
    "is_code_audit_structure_failure",
    "normalize_audit_evidence",
    "normalize_audit_severity",
    "normalize_audit_verdict",
    "parse_audit_json",
    "validate_code_audit_payload",
]
