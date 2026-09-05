"""交付形态 form=prose|files|workspace 派单纪律度量.

背景：委派 worker 的交付形态曾靠提示词两段式措辞，同任务时对时错（打招呼被乱落盘）。
已决策把 ``deliverable.form`` 升为结构化契约；CEO 侧用三档分流：
【看】→prose / 【存文档】→files（默认）/ 【改工程】→workspace。漏填=files。

本模块把「CEO 是否按分流正确声明 form、落盘任务是否点明写文件」变成可复跑信号：

1. **合成样本**（:data:`SAMPLES`）：打招呼 / 纯分析类（prose）、存文档（files）、
   改工程（workspace）。
2. **直连** ``provider.complete``（无工具）——让模型只输出 JSON
   ``{"form":"prose"|"files"|"workspace","task":"..."}``，不跑完整 ReAct。
3. **合规检查**（:func:`check_form_call`）：form 对齐期望；files/workspace 时 task 须含落盘指示。

真跑需平台 / ``EVAL_DEEPSEEK_API_KEY``；单测注入脚本化假 provider 零成本验证检查器与样本集。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from agentcore.evals.types import EvalConfigError
from agentcore.llm.provider.protocol import LLMMessage, LLMProvider, LLMRequest
from agentcore.runtime.runs.contract import describe_deliverable
from agentcore.runtime.runs.types import Deliverable
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin.delegate.schema import (
    DELEGATE_DESCRIPTION,
    TASK_DELIVERABLE_SCHEMA,
)

_FORM_RE = re.compile(r'"form"\s*:\s*"(prose|files|workspace)"', re.IGNORECASE)
_TASK_RE = re.compile(r'"task"\s*:\s*"((?:\\.|[^"\\])*)"', re.DOTALL)
_LANDING_HINT = re.compile(
    r"file_write|写入工作区|写进工作区|"
    r"(?:必须|请|务必)(?:调用\s*)?file_write|"
    r"(?:必须|请|务必).{0,6}落盘|"
    r"用\s*file_write",
    re.IGNORECASE,
)

_CLASSIFIER_SYSTEM = (
    "你是 CEO Agent，正在决定如何委派一条子任务。根据用户请求，输出【且仅输出】一个 JSON 对象：\n"
    '{"form":"prose"|"files"|"workspace","task":"<交给 worker 的任务描述>"}\n'
    "\n"
    "分流（三档；漏填=files；禁止扫用户原话补 prose）：\n"
    "- 【看】（回答 / 分析 / 汇报 / 创意文字 / 打招呼）→ form=prose；"
    "task 写清用正文交付，不要要求落盘。\n"
    "- 【存文档】（报告 / 笔记 / 网页成品等要保存的文件）→ form=files；"
    "task 必须点明用 file_write 把产物写进工作区。\n"
    "- 【改工程】（改用户工程树：源码 / 测试 / 配置）→ form=workspace；"
    "task 必须点明用 file_write 把改动写进工作区。\n"
    "\n"
    "不要输出其它文字、不要 markdown 代码围栏。"
)


@dataclass(frozen=True)
class DeliverableFormSample:
    """一条交付形态派单度量样本。"""

    id: str
    user_prompt: str
    expected_form: str  # prose | files | workspace
    # files 样本：模型写出的 task 是否应含落盘指示（默认 True）
    expect_landing_hint: bool = True

    def build_messages(self) -> tuple[str, str]:
        return _CLASSIFIER_SYSTEM, self.user_prompt


SAMPLES: tuple[DeliverableFormSample, ...] = (
    DeliverableFormSample(
        id="greet_team",
        user_prompt="让每个 AI 给我打招呼",
        expected_form="prose",
        expect_landing_hint=False,
    ),
    DeliverableFormSample(
        id="greet_roles",
        user_prompt="请分别以产品经理、设计师、工程师的口吻各说一句开场白",
        expected_form="prose",
        expect_landing_hint=False,
    ),
    DeliverableFormSample(
        id="analyze_risk",
        user_prompt="帮我分析一下远程办公对协作效率的利弊，给我一段文字结论即可",
        expected_form="prose",
        expect_landing_hint=False,
    ),
    DeliverableFormSample(
        id="compare_options",
        user_prompt="对比 Postgres 和 MySQL 在我们场景下的取舍，写成简短分析回复我",
        expected_form="prose",
        expect_landing_hint=False,
    ),
    DeliverableFormSample(
        id="review_copy",
        user_prompt="审一下这段文案有没有问题，直接把意见写给我看",
        expected_form="prose",
        expect_landing_hint=False,
    ),
    DeliverableFormSample(
        id="build_landing",
        user_prompt="做一个可打开的落地页，HTML 单文件就行",
        expected_form="files",
    ),
    DeliverableFormSample(
        id="write_script",
        user_prompt="写一个 Python 脚本帮我批量重命名文件，保存到工作区我好运行",
        expected_form="files",
    ),
    DeliverableFormSample(
        id="scaffold_api",
        user_prompt="在现有项目里搭一个最小 FastAPI hello 服务，直接改工程源码",
        expected_form="workspace",
    ),
    DeliverableFormSample(
        id="fix_css",
        user_prompt="改一下 styles.css 把主色换成深蓝，直接改工程文件",
        expected_form="workspace",
    ),
    DeliverableFormSample(
        id="readme_md",
        user_prompt="给这个项目写一份 README.md 落到仓库里",
        expected_form="workspace",
    ),
    DeliverableFormSample(
        id="patch_login_bug",
        user_prompt="现有仓库登录页有 bug，直接改源码修好",
        expected_form="workspace",
    ),
)


@dataclass(frozen=True)
class FormCheckResult:
    ok: bool
    failures: tuple[str, ...] = ()
    form: str = ""
    task: str = ""


def check_form_call(
    text: str,
    *,
    expected_form: str,
    expect_landing_hint: bool = True,
) -> FormCheckResult:
    """确定性检查模型输出的 form/task JSON（无 LLM）。"""
    raw = (text or "").strip()
    failures: list[str] = []
    if not raw:
        return FormCheckResult(ok=False, failures=("empty",))

    form = ""
    task = ""
    try:
        # Tolerate accidental markdown fences.
        cleaned = raw
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)
        if isinstance(data, dict):
            form = str(data.get("form") or "").strip().lower()
            task = str(data.get("task") or "").strip()
    except json.JSONDecodeError:
        m_form = _FORM_RE.search(raw)
        m_task = _TASK_RE.search(raw)
        form = (m_form.group(1).lower() if m_form else "")
        task = (m_task.group(1).encode().decode("unicode_escape") if m_task else "")

    if form not in ("prose", "files", "workspace"):
        failures.append("missing_or_invalid_form")
    elif form != expected_form:
        failures.append(f"form_mismatch:got={form}:want={expected_form}")

    if expected_form in ("files", "workspace") and expect_landing_hint:
        if not task:
            failures.append("missing_task")
        elif not _LANDING_HINT.search(task):
            failures.append("files_task_missing_landing_hint")

    if expected_form == "prose" and task and _LANDING_HINT.search(task):
        # Soft smell: prose task should not push file_write; still a failure signal.
        failures.append("prose_task_has_landing_hint")

    return FormCheckResult(ok=not failures, failures=tuple(failures), form=form, task=task)


def check_prompt_contract() -> list[str]:
    """零 LLM：生产提示 / schema / 交付物规格含 form 分流契约。"""
    gaps: list[str] = []
    # Form routing lives in delegate schema; skill only keeps the review exception.
    orch = build_system_skill_registry().get("team_orchestration_advanced")
    orch_body = orch.body if orch is not None else ""
    if "form=prose" not in orch_body:
        gaps.append("team_orchestration_missing_review_form_exception")
    # workspace / prose 档权威在 schema「【看】/【改工程】」；
    # 做软件专段已撤，skill 不再点名 form=workspace。
    if "自动静态质检" in orch_body or "可开 web_quality_scan" in orch_body:
        gaps.append("team_orchestration_still_teaches_web_quality_scan")
    if "required_sections" in orch_body:
        gaps.append("team_orchestration_still_teaches_required_sections")
    if "form" not in TASK_DELIVERABLE_SCHEMA.get("properties", {}):
        gaps.append("schema_missing_form")
    else:
        enum = TASK_DELIVERABLE_SCHEMA["properties"]["form"].get("enum")  # type: ignore[index]
        if enum != ["prose", "files", "workspace"]:
            gaps.append(f"schema_form_enum:{enum}")
        props = TASK_DELIVERABLE_SCHEMA["properties"]
        for banned in (
            "required_sections",
            "output_format",
            "strict",
            "citation_mode",
            "workspace_native",
            "artifact_dir",
        ):
            if banned in props:
                gaps.append(f"schema_still_exposes_{banned}")
    if "才用本工具" in DELEGATE_DESCRIPTION:
        gaps.append("delegate_desc_still_implies_file_artifact_only")
    # 三档只留参数面（tasks.deliverable.form），工具 description 不再抄。
    form_hint = ""
    form_props = TASK_DELIVERABLE_SCHEMA.get("properties") or {}
    if isinstance(form_props, dict):
        form_field = form_props.get("form") or {}
        if isinstance(form_field, dict):
            form_hint = str(form_field.get("description") or "")
    if "【看】" not in form_hint and "prose" not in form_hint:
        gaps.append("delegate_param_missing_form_hint")
    if "【存文档】" not in form_hint and "【改工程】" not in form_hint:
        gaps.append("delegate_param_missing_three_tier")
    prose = describe_deliverable(Deliverable(form="prose"))
    files = describe_deliverable(Deliverable(form="files"))
    omitted = describe_deliverable(Deliverable())
    workspace = describe_deliverable(Deliverable(form="workspace"))
    if "file_write" in prose:
        gaps.append("prose_spec_still_mentions_file_write")
    if "成品写入工作区" not in files:
        gaps.append("files_spec_missing_landing_contract")
    if "form=files" not in omitted or "可独立阅读的文字" in omitted:
        gaps.append("omit_spec_not_files")
    if "form=workspace" not in workspace or "AgentCore/文档" not in workspace:
        gaps.append("workspace_spec_missing_in_place_copy")
    return gaps


@dataclass
class SampleJudgement:
    id: str
    expected_form: str
    ok: bool
    failures: tuple[str, ...]
    form: str
    task: str
    content: str
    content_preview: str = ""

    def __post_init__(self) -> None:
        preview = (self.content or "").strip().replace("\n", "\\n")
        object.__setattr__(self, "content_preview", preview[:160])


@dataclass
class DeliverableFormMetrics:
    per: list[SampleJudgement] = field(default_factory=list)
    prompt_gaps: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.per)

    @property
    def n_ok(self) -> int:
        return sum(1 for x in self.per if x.ok)

    @property
    def compliance_rate(self) -> float:
        return self.n_ok / self.n if self.n else 0.0

    @property
    def failures(self) -> list[SampleJudgement]:
        return [x for x in self.per if not x.ok]


def lint_samples(samples: Sequence[DeliverableFormSample] = SAMPLES) -> None:
    """零 LLM：样本集结构 + 生产契约静态门禁。"""
    if len(samples) < 8:
        raise EvalConfigError(f"deliverable_form 样本不足 8 条（got {len(samples)}）")
    ids = [s.id for s in samples]
    if len(ids) != len(set(ids)):
        raise EvalConfigError("deliverable_form 样本 id 不唯一")
    forms = {s.expected_form for s in samples}
    if forms != {"prose", "files", "workspace"}:
        raise EvalConfigError(f"样本须同时覆盖 prose、files 与 workspace（got {forms}）")
    for s in samples:
        if s.expected_form not in ("prose", "files", "workspace"):
            raise EvalConfigError(f"{s.id}: expected_form 非法 {s.expected_form!r}")
        system, user = s.build_messages()
        if "【看】" not in system or "【存文档】" not in system or "【改工程】" not in system:
            raise EvalConfigError(f"{s.id}: classifier system 缺三档分流")
        if not user.strip():
            raise EvalConfigError(f"{s.id}: user_prompt 为空")
    gaps = check_prompt_contract()
    if gaps:
        raise EvalConfigError("deliverable_form 生产契约缺口：" + "；".join(gaps))


async def run_deliverable_form(
    provider: LLMProvider,
    model: str,
    samples: Sequence[DeliverableFormSample] = SAMPLES,
) -> DeliverableFormMetrics:
    """对每个样本直连 complete，检查 form / 落盘指示。"""
    if not samples:
        raise EvalConfigError("deliverable_form 样本集为空")
    prompt_gaps = check_prompt_contract()
    per: list[SampleJudgement] = []
    for s in samples:
        system, user = s.build_messages()
        request = LLMRequest(
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=user),
            ],
            model=model,
            temperature=0.2,
            stream=False,
            tools=None,
            scenario="eval.deliverable_form",
        )
        response = await provider.complete(request)
        content = (response.content or "").strip()
        checked = check_form_call(
            content,
            expected_form=s.expected_form,
            expect_landing_hint=s.expect_landing_hint,
        )
        per.append(
            SampleJudgement(
                id=s.id,
                expected_form=s.expected_form,
                ok=checked.ok,
                failures=checked.failures,
                form=checked.form,
                task=checked.task,
                content=content,
            )
        )
    return DeliverableFormMetrics(per=per, prompt_gaps=prompt_gaps)


def _form_provider_and_model(mode: str = "quality") -> tuple[LLMProvider, str]:
    import os

    from agentcore.config import settings
    from agentcore.evals.eval_modes import resolve_profile_set
    from agentcore.evals.harness import _EVAL_CEILING, _eval_credentials
    from agentcore.llm.factory import build_provider

    provider = build_provider(_eval_credentials())
    model = os.environ.get("EVAL_DELIVERABLE_FORM_MODEL", "").strip()
    if not model:
        model = os.environ.get("EVAL_DEBATE_MODEL", "").strip()
    if not model:
        model = (settings.platform_model or "").strip()
    if not model:
        profiles = resolve_profile_set(mode, custom_modes={}, ceiling=_EVAL_CEILING)
        model = profiles.model_for("agent")
    return provider, model


def deliverable_form_to_dict(m: DeliverableFormMetrics) -> dict:
    return {
        "n": m.n,
        "n_ok": m.n_ok,
        "compliance_rate": round(m.compliance_rate, 4),
        "prompt_gaps": list(m.prompt_gaps),
        "per_sample": [
            {
                "id": x.id,
                "expected_form": x.expected_form,
                "ok": x.ok,
                "failures": list(x.failures),
                "form": x.form,
                "task_preview": (x.task or "")[:120],
                "content_preview": x.content_preview,
            }
            for x in m.per
        ],
    }


def format_deliverable_form_report(m: DeliverableFormMetrics) -> str:
    lines = [
        f"[deliverable_form] n={m.n} ok={m.n_ok} rate={m.compliance_rate:.1%}",
    ]
    if m.prompt_gaps:
        lines.append("prompt_gaps: " + "; ".join(m.prompt_gaps))
    for x in m.failures[:8]:
        lines.append(
            f"  FAIL {x.id}: {', '.join(x.failures)} | got={x.form!r} preview={x.content_preview}"
        )
    return "\n".join(lines)
