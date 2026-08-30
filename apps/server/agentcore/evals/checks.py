"""确定性 Check（评估体系 §五）：判定无需 LLM.

每个 Check 从 ``{"name", "args"}`` 规格经 :func:`build_check` 实例化；注册表的键集
（:data:`CHECK_NAMES`）供 ``seed_lint`` 校验用例里引用的 check 名是否存在。
Check 读 :class:`TurnOutcome`，返回 :class:`CheckOutcome`。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from agentcore.evals.style_lint import style_violations
from agentcore.evals.types import CheckOutcome, EvalCase, TurnOutcome


@dataclass
class FinishReasonCheck:
    """回合正常收口（默认 ``end_turn``，即非 error / max_rounds / degraded / unproductive）。"""

    expected: str = "end_turn"
    name: str = "FinishReason"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        ok = outcome.finish_reason == self.expected
        return CheckOutcome(self.name, ok, f"finish_reason={outcome.finish_reason!r}")


@dataclass
class NonEmptyCheck:
    """回复正文非空、长度达阈值。"""

    min_len: int = 1
    name: str = "NonEmpty"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        n = len((outcome.content or "").strip())
        return CheckOutcome(self.name, n >= self.min_len, f"len={n} (min {self.min_len})")


@dataclass
class ToolCalledCheck:
    """调用了指定工具（按工具名匹配，至少一次）。"""

    tool: str = ""
    name: str = "ToolCalled"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        names = [t[0] for t in outcome.tool_calls]
        ok = self.tool in names
        return CheckOutcome(self.name, ok, f"want {self.tool!r} in {names}")


@dataclass
class ToolArgsValidCheck:
    """指定工具的入参 JSON 合法、且含必填键（``tool`` 为空时校验所有工具调用）。"""

    tool: str | None = None
    required: list[str] = field(default_factory=list)
    name: str = "ToolArgsValid"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        matched = [(n, a) for (n, a) in outcome.tool_calls if self.tool is None or n == self.tool]
        if not matched:
            return CheckOutcome(self.name, False, f"no call to {self.tool!r}")
        for n, raw in matched:
            try:
                args = json.loads(raw) if raw else {}
            except json.JSONDecodeError as e:
                return CheckOutcome(self.name, False, f"{n}: bad JSON ({e})")
            missing = [k for k in self.required if k not in args]
            if missing:
                return CheckOutcome(self.name, False, f"{n}: missing {missing}")
        return CheckOutcome(self.name, True, f"{len(matched)} call(s) valid")


# ``equals`` 未设置的哨兵：与合法期望值 ``None`` 区分（``ToolArgEquals`` 可断言参数为 null）。
_EQUALS_UNSET: Any = object()


@dataclass
class ToolArgNonEmptyCheck:
    """指定工具的某次调用，入参 ``arg`` 的存在性 / 取值断言（两种模式共用本类）。

    - **非空**（默认，注册名 ``ToolArgNonEmpty``）：``arg`` 存在且**非空**（非空 list/str/dict
      等真值）。比 ``ToolArgsValid.required``（仅查键是否存在）更强——典型用途是验证 escalate
      的结构化 ``questions``。非法 JSON 跳过该次调用。
    - **精确相等**（``equals`` 已设，注册名 ``ToolArgEquals``）：``arg`` 存在且 ``== equals``。
      用途是精确量「consult 拉的是哪一条」——区分拉对 vs 拉错但蒙对答案。非法 JSON 直接失败。

    任一匹配调用满足即通过。
    """

    tool: str = ""
    arg: str = ""
    equals: Any = field(default=_EQUALS_UNSET)
    name: str = "ToolArgNonEmpty"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        want_equals = self.equals is not _EQUALS_UNSET
        matched = [(n, a) for (n, a) in outcome.tool_calls if n == self.tool]
        if not matched:
            return CheckOutcome(self.name, False, f"no call to {self.tool!r}")
        for n, raw in matched:
            try:
                args = json.loads(raw) if raw else {}
            except json.JSONDecodeError as e:
                if want_equals:
                    return CheckOutcome(self.name, False, f"{n}: bad JSON ({e})")
                continue
            if want_equals:
                if self.arg in args and args[self.arg] == self.equals:
                    return CheckOutcome(self.name, True, f"{n}.{self.arg}=={self.equals!r}")
            elif args.get(self.arg):  # truthy ⇒ present & non-empty (空 list/str/dict 为假)
                return CheckOutcome(self.name, True, f"{n}.{self.arg} non-empty")
        if want_equals:
            return CheckOutcome(
                self.name, False, f"{self.tool}.{self.arg}!={self.equals!r} in all calls"
            )
        return CheckOutcome(self.name, False, f"{self.tool}.{self.arg} empty/missing in all calls")


@dataclass
class HasCitationsCheck:
    """引用数达阈值（检索类用例）。"""

    min_count: int = 1
    name: str = "HasCitations"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        n = len(outcome.citations)
        return CheckOutcome(self.name, n >= self.min_count, f"citations={n} (min {self.min_count})")


@dataclass
class DelegatedCheck:
    """本回合确实委派了团队。"""

    name: str = "Delegated"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        return CheckOutcome(self.name, outcome.delegated, f"delegated={outcome.delegated}")


@dataclass
class NotDelegatedCheck:
    """本回合**没有**委派团队（``DelegatedCheck`` 的护栏逆否）。

    探测「过度编排」——简单问题本该 CEO 直接答，却拆成一支团队，是 Multi-Agent 产品
    最典型的体验/成本灾难。须走 ``path="team"`` 才有意义（``single`` 路径恒不委派、
    断言会平凡通过）；功能套件据此守住「简单问题零编排」这条护栏。
    """

    name: str = "NotDelegated"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        return CheckOutcome(self.name, not outcome.delegated, f"delegated={outcome.delegated}")


@dataclass
class DelegateCriteriaForbiddenCheck:
    """S3：``completion_criteria`` 字段已删；任一 ``delegate`` 不得再传该顶层键。

    兼容旧 eval 的 ``forbid`` 列表：若仍传了对象且 ``type`` 落在 forbid 也失败；
    主路径是「键不存在」。无 ``delegate`` 调用则失败。
    """

    forbid: list[str] = field(default_factory=list)
    name: str = "DelegateCriteriaForbidden"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        matched = [(n, a) for (n, a) in outcome.tool_calls if n == "delegate"]
        if not matched:
            return CheckOutcome(self.name, False, "no delegate call")
        forbidden = {str(x) for x in self.forbid}
        for _n, raw in matched:
            try:
                args = json.loads(raw) if raw else {}
            except json.JSONDecodeError as e:
                return CheckOutcome(self.name, False, f"delegate: bad JSON ({e})")
            if "completion_criteria" not in args:
                continue
            cc = args.get("completion_criteria")
            kind = None
            if isinstance(cc, str):
                kind = cc
            elif isinstance(cc, dict):
                kind = cc.get("type") or cc.get("kind")
            if forbidden and kind is not None and str(kind) in forbidden:
                return CheckOutcome(
                    self.name,
                    False,
                    f"completion_criteria.type={kind!r} forbidden ({sorted(forbidden)})",
                )
            return CheckOutcome(
                self.name,
                False,
                "completion_criteria retired (S3); omit the field",
            )
        return CheckOutcome(self.name, True, "no completion_criteria")


@dataclass
class RosterMatchesCheck:
    """实际委派出的角色覆盖期望角色（``roster ⊇ expected``）。"""

    expected: list[str] = field(default_factory=list)
    name: str = "RosterMatches"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        actual = set(outcome.roster)
        missing = [r for r in self.expected if r not in actual]
        ok = not missing
        return CheckOutcome(self.name, ok, f"roster={outcome.roster}, missing={missing}")


@dataclass
class ShapeMatchesCheck:
    """规划 DAG 相对期望形状的匹配分（报告/诊断，不进 L0 门禁）.

    读 ``case.expected_shape``（或 ``args`` 覆盖）+ ``outcome.plan_runs``，经
    ``shape_score.score_shape`` 得 0~1。``passed`` = 分数 ≥ ``threshold``（默认 0.6），但本
    Check 在 ``DIAGNOSTIC_CHECKS`` 中，不计入用例 pass/fail。
    """

    threshold: float = 0.6
    name: str = "ShapeMatches"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        from agentcore.evals.shape_score import score_shape

        expected = case.expected_shape
        result = score_shape(outcome.plan_runs, expected, plan_type=outcome.plan_type)
        ok = result.score >= self.threshold
        return CheckOutcome(
            self.name,
            ok,
            f"score={result.score:.2f} (阈 {self.threshold:.2f}); {result.summary}",
        )


@dataclass
class MaxRoundsCheck:
    """轮数不超过预算（探测空转 / 收敛差）。"""

    budget: int = 16
    name: str = "MaxRounds"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        ok = outcome.rounds <= self.budget
        return CheckOutcome(self.name, ok, f"rounds={outcome.rounds} (budget {self.budget})")


@dataclass
class MaxToolCallsCheck:
    """工具调用总数不超过预算（探测检索 / 工具滥用——团队任务尤甚）。

    读 ``outcome.tool_calls`` 长度（含被委派 worker 的调用，由 ``RecordingSink`` 全量截获）。
    与 ``MaxRounds`` 正交：轮数看 ReAct 节奏，工具数看「检索 / 读取是否泛滥」——一道团队任务
    打数十次 ``web_search`` 的成本 / 延迟灾难，靠它才可量化、可回归。
    """

    budget: int = 24
    name: str = "MaxToolCalls"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        n = len(outcome.tool_calls)
        return CheckOutcome(self.name, n <= self.budget, f"tool_calls={n} (budget {self.budget})")


@dataclass
class StyleCleanCheck:
    """回复无 anti-slop 风格违规（方向④确定性护栏）。

    跑 ``style_lint.style_violations`` 检测套话开场 / 客套收尾 / 未授权 emoji（纯文本、零
    LLM，详见 ``style_lint.py``）。``args.allow`` 可豁免规则——典型是用户自己用了 emoji 时
    放行 ``"emoji"``，与 ``<输出>`` 的 emoji soft carve-out 对齐。
    """

    allow: list[str] = field(default_factory=list)
    name: str = "StyleClean"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        violations = [v for v in style_violations(outcome.content) if v.rule not in self.allow]
        ok = not violations
        detail = "clean" if ok else "; ".join(f"{v.rule}:{v.snippet}" for v in violations)
        return CheckOutcome(self.name, ok, detail)


@dataclass
class NoFabricationMarkerCheck:
    """回复不含编造痕迹（确定性子集：禁用短语命中即判失败）。

    完整的「不编造」靠 LLM 裁判（§六）；本 Check 只兜确定性可判的明显信号——例如声称
    使用了未提供的工具/能力的固定话术，经 ``args.forbidden`` 配置。空列表则恒过。
    """

    forbidden: list[str] = field(default_factory=list)
    name: str = "NoFabricationMarker"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        text = outcome.content or ""
        hit = [p for p in self.forbidden if p in text]
        return CheckOutcome(self.name, not hit, f"forbidden hits={hit}")


@dataclass
class ContentMatchesCheck:
    """回复正文匹配/不匹配给定正则——**确定性的「答案对不对」校验**。

    评估套件原本只有结构 / 轨迹类 Check（收口、工具、引用数、roster、轮数），**没有**「交付
    物语义上对不对」这一维：一份答案错了、却结构完整（过得了轻层 ``finish_guard`` 的代码围栏
    闭合 + 角标越界两查），现有 Check 一律放行。本 Check 用一个**已知正确答案**的正则在正文上
    ``re.search``——``negate=False`` 要求命中（正确答案出现即过）、``negate=True`` 要求**不**命
    中（探测某个错误答案没出现）。``flags`` 取 ``"i"``（忽略大小写）/``"s"``（``.`` 跨行）/
    ``"m"``（多行），可组合（如 ``"is"``）。

    主用途是「挖坑」探针（远期规划.md §2.5 重层立项证据）：给一道有唯一可判答案的任务（第 N
    个素数 / 复利终值 / 大数乘法 / 日期推算），用本 Check 当确定性地面真值，量化「回合过了轻层
    却答错」的缺陷率——那正是机械轻层够不着、需重层（要跑 / 要重算 / 回源对照）才拦得住的那一类。
    """

    pattern: str = ""
    negate: bool = False
    flags: str = ""
    name: str = "ContentMatches"

    _FLAG_BITS = {"i": re.IGNORECASE, "s": re.DOTALL, "m": re.MULTILINE}

    def _flag_value(self) -> int:
        bits = 0
        for ch in self.flags:
            bits |= self._FLAG_BITS.get(ch, 0)
        return bits

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        text = outcome.content or ""
        try:
            hit = re.search(self.pattern, text, self._flag_value()) is not None
        except re.error as e:
            return CheckOutcome(self.name, False, f"bad regex {self.pattern!r}: {e}")
        ok = (not hit) if self.negate else hit
        verb = "must-not-match" if self.negate else "must-match"
        return CheckOutcome(self.name, ok, f"{verb} {self.pattern!r} -> {'hit' if hit else 'miss'}")


@dataclass
class DeliverableIntegrityCheck:
    """成品完整性（确定性）：禁省略标记 + 同 path 连续 ``file_write`` 字数骤降。

    复用 ``file_ops.has_omission_marker`` / ``is_severe_shrink``——与生产 overwrite soft
    nudge 同语义。扫 ``outcome.content`` 与全部 ``file_write`` 入参正文；任一含省略标记即
    不过。字数骤降仅在同一 path **有旧稿**（≥2 次 write）时比末次 vs 前次；无旧稿跳过该维，
    避免首写误报。默认 gating（不进 ``DIAGNOSTIC_CHECKS``）。
    """

    name: str = "DeliverableIntegrity"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        from agentcore.tools.builtin.file_ops import has_omission_marker, is_severe_shrink

        reasons: list[str] = []

        content = outcome.content or ""
        if has_omission_marker(content):
            reasons.append("omission in content")

        # path → 按调用序累积的 content 列表（仅成功解析的 file_write）
        writes_by_path: dict[str, list[str]] = {}
        for tool_name, raw in outcome.tool_calls:
            if tool_name != "file_write":
                continue
            try:
                args = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                continue
            if not isinstance(args, dict):
                continue
            body = args.get("content")
            if not isinstance(body, str):
                continue
            if has_omission_marker(body):
                path = args.get("path") if isinstance(args.get("path"), str) else "?"
                reasons.append(f"omission in file_write({path})")
            path = args.get("path")
            if isinstance(path, str) and path:
                writes_by_path.setdefault(path, []).append(body)

        for path, bodies in writes_by_path.items():
            if len(bodies) < 2:
                continue  # 无旧稿：跳过 shrink 维
            old_chars = len(bodies[-2])
            new_chars = len(bodies[-1])
            if is_severe_shrink(old_chars, new_chars):
                reasons.append(f"severe shrink {path} ({old_chars}→{new_chars})")

        if reasons:
            return CheckOutcome(self.name, False, "; ".join(reasons))
        return CheckOutcome(self.name, True, "ok")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _protected_file_map(root: Path, rel_paths: list[str]) -> dict[str, str]:
    """相对根的受保护文件 → sha256（键用 posix 相对路径，跨平台稳定）。"""
    out: dict[str, str] = {}
    for rel in rel_paths:
        target = (root / rel).resolve()
        root_resolved = root.resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError:
            continue
        if target.is_file():
            key = PurePosixPath(Path(rel).as_posix()).as_posix()
            out[key] = _file_sha256(target)
            continue
        if not target.is_dir():
            continue
        for f in sorted(target.rglob("*")):
            if not f.is_file():
                continue
            # 跳过缓存/编译产物，避免误报「测目录被改」
            parts = {p.lower() for p in f.parts}
            if parts & {".pytest_cache", "__pycache__", ".mypy_cache", "node_modules"}:
                continue
            if f.suffix in {".pyc", ".pyo"}:
                continue
            rel_key = f.relative_to(root_resolved).as_posix()
            out[rel_key] = _file_sha256(f)
    return out


@dataclass
class TestExitCodeCheck:
    """在 ``outcome.workspace_root`` 跑约定测试命令，断言进程退出码（真仓 Fix 硬判据）.

    ``command`` 为 argv 列表（Windows 友好：不经 shell）。缺省期望 ``expected_exit=0``。
    ``pythonpath`` 为相对工作区的目录列表（默认 ``["."]``）；click 等 src layout 用 ``["src"]``。
    工作区未挂载 → 失败（避免「没跑测却过」）。
    """

    command: list[str] = field(default_factory=list)
    expected_exit: int = 0
    timeout_sec: int = 120
    pythonpath: list[str] = field(default_factory=lambda: ["."])
    name: str = "TestExitCode"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        if not self.command:
            return CheckOutcome(self.name, False, "command empty")
        root = outcome.workspace_root
        if not root:
            return CheckOutcome(self.name, False, "workspace_root missing")
        cwd = Path(root)
        if not cwd.is_dir():
            return CheckOutcome(self.name, False, f"workspace_root not a dir: {root}")
        # 与产品 code_execute 同源：相对 cwd 解析 pythonpath（卡声明或默认 ["."]）
        from agentcore.tools.sandbox.pythonpath import merge_pythonpath_into_env

        env = merge_pythonpath_into_env(
            cwd, os.environ.copy(), rels=list(self.pythonpath or ["."])
        )
        try:
            proc = subprocess.run(
                list(self.command),
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
        except FileNotFoundError as e:
            return CheckOutcome(self.name, False, f"exec failed: {e}")
        except subprocess.TimeoutExpired:
            return CheckOutcome(self.name, False, f"timeout>{self.timeout_sec}s")
        ok = proc.returncode == self.expected_exit
        tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]
        detail = f"exit={proc.returncode} want={self.expected_exit}; {tail.strip()}"
        return CheckOutcome(self.name, ok, detail)


@dataclass
class TestsUnchangedCheck:
    """禁改测目录作弊闸：工作区受保护路径须与 ``reference_root`` 逐文件哈希一致。

    典型 ``paths=["tests"]``（或仓约定测目录）。缺 ``workspace_root`` / ``reference_root``
    → 失败。只比受保护树，不限制生产代码修复。

    ``allow_extra``：允许工作区多出的相对路径（posix），供 Extend 追加 GOLDEN 测文件；
    仍禁止改/删 upstream 测。白名单外的 extra / 任意 changed / missing → 失败。
    """

    paths: list[str] = field(default_factory=lambda: ["tests"])
    allow_extra: list[str] = field(default_factory=list)
    name: str = "TestsUnchanged"

    def run(self, case: EvalCase, outcome: TurnOutcome) -> CheckOutcome:
        if not outcome.workspace_root:
            return CheckOutcome(self.name, False, "workspace_root missing")
        if not outcome.reference_root:
            return CheckOutcome(self.name, False, "reference_root missing")
        ws = Path(outcome.workspace_root)
        ref = Path(outcome.reference_root)
        if not ws.is_dir() or not ref.is_dir():
            return CheckOutcome(self.name, False, "workspace/reference not dirs")
        left = _protected_file_map(ws, self.paths)
        right = _protected_file_map(ref, self.paths)
        allow = {PurePosixPath(p).as_posix() for p in self.allow_extra}
        missing = sorted(set(right) - set(left))
        extra = sorted(k for k in (set(left) - set(right)) if k not in allow)
        changed = sorted(k for k in (set(left) & set(right)) if left[k] != right[k])
        if not missing and not extra and not changed:
            allowed_n = len(set(left) - set(right))
            detail = f"ok ({len(right)} upstream"
            if allowed_n:
                detail += f", {allowed_n} allow_extra"
            detail += ")"
            return CheckOutcome(self.name, True, detail)
        bits: list[str] = []
        if changed:
            bits.append(f"changed={changed[:8]}")
        if missing:
            bits.append(f"missing={missing[:8]}")
        if extra:
            bits.append(f"extra={extra[:8]}")
        return CheckOutcome(self.name, False, "; ".join(bits) or "mismatch")


# 注册表：check 名 → 从 args 构造实例。新增 Check 在此登记，seed_lint 据键集校验。
_REGISTRY: dict[str, Callable[[dict[str, Any]], Any]] = {
    "FinishReason": lambda a: FinishReasonCheck(expected=a.get("expected", "end_turn")),
    "NonEmpty": lambda a: NonEmptyCheck(min_len=int(a.get("min_len", 1))),
    "ToolCalled": lambda a: ToolCalledCheck(tool=a.get("tool", "")),
    "ToolArgsValid": lambda a: ToolArgsValidCheck(
        tool=a.get("tool"), required=list(a.get("required", []))
    ),
    "ToolArgNonEmpty": lambda a: ToolArgNonEmptyCheck(
        tool=a.get("tool", ""), arg=a.get("arg", "")
    ),
    "ToolArgEquals": lambda a: ToolArgNonEmptyCheck(
        tool=a.get("tool", ""),
        arg=a.get("arg", ""),
        equals=a.get("equals"),
        name="ToolArgEquals",
    ),
    "HasCitations": lambda a: HasCitationsCheck(min_count=int(a.get("min", 1))),
    "Delegated": lambda a: DelegatedCheck(),
    "NotDelegated": lambda a: NotDelegatedCheck(),
    "DelegateCriteriaForbidden": lambda a: DelegateCriteriaForbiddenCheck(
        forbid=list(a.get("forbid", []))
    ),
    "RosterMatches": lambda a: RosterMatchesCheck(expected=list(a.get("expected", []))),
    "ShapeMatches": lambda a: ShapeMatchesCheck(threshold=float(a.get("threshold", 0.6))),
    "MaxRounds": lambda a: MaxRoundsCheck(budget=int(a.get("budget", 16))),
    "MaxToolCalls": lambda a: MaxToolCallsCheck(budget=int(a.get("budget", 24))),
    "NoFabricationMarker": lambda a: NoFabricationMarkerCheck(
        forbidden=list(a.get("forbidden", []))
    ),
    "StyleClean": lambda a: StyleCleanCheck(allow=list(a.get("allow", []))),
    "ContentMatches": lambda a: ContentMatchesCheck(
        pattern=a.get("pattern", ""),
        negate=bool(a.get("negate", False)),
        flags=a.get("flags", ""),
    ),
    "DeliverableIntegrity": lambda a: DeliverableIntegrityCheck(),
    "TestExitCode": lambda a: TestExitCodeCheck(
        command=list(a.get("command") or []),
        expected_exit=int(a.get("expected_exit", 0)),
        timeout_sec=int(a.get("timeout_sec", 120)),
        pythonpath=list(a.get("pythonpath") or ["."]),
    ),
    "TestsUnchanged": lambda a: TestsUnchangedCheck(
        paths=list(a.get("paths") or ["tests"]),
        allow_extra=list(a.get("allow_extra") or []),
    ),
}

CHECK_NAMES: frozenset[str] = frozenset(_REGISTRY)

# 诊断 Check（轨迹形状）：仍注册、仍跑、仍报告，但**不计入** pass/fail（后端架构.md §五）。
# 「派没派 / roster 对不对」是编排手段，不是任务结果——把它当 golden 标签会变成回归测试作者的
# 编排理论（「实现冒充需求」）。过度编排改由「个体贡献=0 + L0 成本预算」度量，期望角色改由 L1
# milestone 覆盖度量。``runner.apply_checks`` 据此集合把对应 CheckOutcome 标为 gating=False。
DIAGNOSTIC_CHECKS: frozenset[str] = frozenset(
    {"Delegated", "NotDelegated", "RosterMatches", "ShapeMatches"}
)

# plan-only 模式下仍有意义的 Check（形状 / 委派）；其余内容类标 n/a、绝不 gating。
PLAN_ONLY_SHAPE_CHECKS: frozenset[str] = frozenset(DIAGNOSTIC_CHECKS)


def build_check(spec: dict[str, Any]) -> Any:
    """从 ``{"name", "args"}`` 规格构造一个 Check 实例（名未注册则 KeyError）。"""
    name = spec["name"]
    args = spec.get("args") or {}
    return _REGISTRY[name](args)
