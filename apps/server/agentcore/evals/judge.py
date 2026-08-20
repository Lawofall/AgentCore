"""LLM 裁判（后端架构.md §五：L1 结果评分主轴 + 多 Agent 诊断成对裁判）.

本文件容纳两类裁判，共用一个 :class:`~agentcore.llm.protocol.LLMProvider`：

- :class:`LLMJudge`（绝对分裁判，**L1 结果评分主轴**）：按 rubric 给 1–5 分、pass = 均分 ≥
  阈值。CoT（先理由后分）+ 反长度偏差系统提示提升与人对齐；``samples>1`` 多采样取均分压裁判
  方差（思考模型不吃 temperature，靠重采样降噪）。这是新评测体系判「任务是否成功」的主轴。
- :class:`LLMPairwiseJudge`（成对偏好裁判，多 Agent 对比**诊断**用）：判「主臂 vs 基准臂」哪个
  更好——盲评 + 位置对调（A/B 换序各判一次，仅当两序一致才计胜，抵消位置偏见）+ 坏 JSON 容错。
- :class:`EnsemblePairwiseJudge`（多评委合议）：包装 N 个 ``PairwiseJudge``，多数决 winner、
  margin 取中位；无多数 → tie。各子裁判若自带 swap，总成本至多 **2N** 次 LLM。

verbosity 准则按用例分流（:func:`resolve_verbosity_policy`）：负样本 / ``simple`` 走
``concise``（简洁正确、打堆砌）；跨域/成篇协作正样本（``cross_domain`` /
``parallel_research`` / ``debate``）走 ``coverage``（覆盖与合成优先，不以更短为胜负主轴）。
缺省 ``concise``，保持向后兼容。

绝对分 / milestone / 成对裁判喂内容统一走 :func:`~agentcore.evals.types.judged_text`
（聊天正文 + 终版 ``artifacts``），与产品文件交付口径对齐；不改写 ``TurnOutcome.content``。

D6（2026-07-20 拍板）：``outcome.citations``（harness 实测检索来源池）非空时，绝对分 /
milestone 裁判的用户提示追加【系统实测检索来源】清单——裁判判 faithfulness 时以此为准，
消除「裁判看不到检索产物 → 把有据答案误判编造」的评测器盲区（retrieval_ai_news 伪 FAIL
根因）。无检索的用例（citations 空）提示逐字节不变，calibration kappa 口径不受影响。

判定本身仍走 ``provider.complete``；单测注入返回固定 JSON 的假 provider，零成本验证解析 /
位置对调合议 / 容错（见 tests/test_evals_comparison.py），真模型留给 nightly。裁判可信工程
（gold-set + Cohen's kappa 校准）已落在 ``calibration.py``
（重设计 §五）；本裁判须先过那里的校准，其 pass_rate 才值得拿去和基线对照。
"""

from __future__ import annotations

import json
import statistics
from typing import Literal

from agentcore.evals.types import (
    EvalCase,
    JudgeVerdict,
    JudgeVote,
    MilestoneItemResult,
    MilestoneVerdict,
    PairwiseJudge,
    PairwiseVerdict,
    TurnOutcome,
    judged_text,
)
from agentcore.llm.provider.protocol import LLMMessage, LLMProvider, LLMRequest

# --- 长度/verbosity 裁判准则分流 ---------------------------------------------
#
# compare 正样本（跨域/成篇协作）与负样本（verbosity 校准）必须分流，否则全局硬编码
# 「简洁正确优于冗长堆砌」会系统性惩罚团队正当长交付。
# - concise：负样本 / simple / 默认——打 verbosity、过度堆砌（向后兼容，或更严）
# - coverage：跨域/并行调研/辩论正样本——覆盖与合成优先；不因实质更长而扣分

VerbosityPolicy = Literal["concise", "coverage"]

# 成篇协作正样本 archetype：覆盖与合成优先，不以「更短」为胜负主轴。
_COVERAGE_ARCHETYPES = frozenset({"cross_domain", "parallel_research", "debate"})
# verbosity 校准 / 负对照：继续（或更严）惩罚冗长堆砌。
_CONCISE_ARCHETYPES = frozenset({"simple"})

# 题面/rubric 显式覆盖标记（优先于 archetype；另有任务改题面时可点名）。
_MARKER_COVERAGE = ("judge_verbosity:coverage", "覆盖与合成优先")
_MARKER_CONCISE = ("judge_verbosity:concise", "verbosity校准")


def resolve_verbosity_policy(
    *,
    archetype: str | None = None,
    rubric: str | None = None,
    case_id: str | None = None,
) -> VerbosityPolicy:
    """按 archetype / 显式标记 / case_id 前缀解析 verbosity 准则；默认 ``concise``（向后兼容）。

    优先级：rubric 显式标记 > case_id 负对照前缀 > archetype > 默认 concise。
    """
    text = rubric or ""
    if any(m in text for m in _MARKER_COVERAGE):
        return "coverage"
    if any(m in text for m in _MARKER_CONCISE):
        return "concise"
    cid = (case_id or "").strip()
    if cid.startswith(("neg_", "trap_")):
        return "concise"
    if archetype in _COVERAGE_ARCHETYPES:
        return "coverage"
    if archetype in _CONCISE_ARCHETYPES:
        return "concise"
    return "concise"


# --- D6：把 harness 实测检索来源喂给裁判 -------------------------------------

# 防提示膨胀：来源清单最多喂这么多条、单条摘要截断长度。
_CITATION_FEED_CAP = 20
_CITATION_SNIPPET_CAP = 200


def _sources_block(outcome: TurnOutcome) -> str:
    """把 ``outcome.citations``（运行时真实检索来源池）排版成裁判可读清单。

    空池返回空串——无检索用例的裁判提示**逐字节不变**（calibration kappa 口径不漂移）。
    序号即池内 1-based 序号，与正文 ``[n]`` 双轨编号一致，裁判可据此核对答案的来源标注。
    """
    cites = outcome.citations or []
    if not cites:
        return ""
    lines: list[str] = []
    for i, c in enumerate(cites[:_CITATION_FEED_CAP], start=1):
        title = str(c.get("title") or "").strip()
        url = str(c.get("url") or "").strip()
        snippet = str(c.get("snippet") or "").strip().replace("\n", " ")
        if len(snippet) > _CITATION_SNIPPET_CAP:
            snippet = snippet[:_CITATION_SNIPPET_CAP] + "…"
        line = f"[{i}] {title} — {url}".rstrip(" —")
        if snippet:
            line += f"\n    摘要: {snippet}"
        lines.append(line)
    overflow = len(cites) - _CITATION_FEED_CAP
    if overflow > 0:
        lines.append(f"（另有 {overflow} 条来源略）")
    return "\n".join(lines)


def _sources_section(outcome: TurnOutcome) -> str:
    """来源清单的提示片段（含判定指引）；无来源时为空串。"""
    block = _sources_block(outcome)
    if not block:
        return ""
    return (
        "【系统实测检索来源】（答案运行时真实检索到的来源，判断是否有据/编造时以此为准，"
        "不要因你自己不知道这些信息就判编造）\n" + block + "\n\n"
    )


# --- L1 结果评分主轴：绝对分裁判 ---------------------------------------------

_ABSOLUTE_SCORE_ANCHOR = (
    "你是严格的质量评审。给定一个任务、评分准则（rubric）和一份答案，按准则给 1–5 分。\n"
    "评分锚点：1=完全没满足/跑题或编造；2=大体没满足；3=部分满足、有明显缺漏；"
    "4=基本满足准则；5=完全满足且无明显问题。\n"
)

_ABSOLUTE_CONCISE_PRINCIPLE = (
    "重要原则：简洁正确优于冗长堆砌——绝不因答案更长、更详细、语气更自信就给高分；"
    "惩罚注水、冗余、重复与过度堆砌；只看是否真正满足准则。\n"
)

_ABSOLUTE_COVERAGE_PRINCIPLE = (
    "重要原则：覆盖与合成优先——以任务要求的覆盖面、证据整合与可交付完整度为准；"
    "惩罚半成品、过程元话语（叙述讨论过程/下一步而无实质交付）、注水与重复堆砌；"
    "不因答案实质更长、更详细就扣分，也不因更短就自动给高分；只看是否真正满足准则。\n"
)

_ABSOLUTE_JSON_TAIL = (
    "先写一句简短理由，再给分。只输出 JSON，不要其他文字：\n"
    '{"score": <1-5 整数>, "rationale": "简短理由"}'
)

# 默认 = concise（向后兼容：未分流路径行为不变）
_ABSOLUTE_SYSTEM_PROMPT = (
    _ABSOLUTE_SCORE_ANCHOR + _ABSOLUTE_CONCISE_PRINCIPLE + _ABSOLUTE_JSON_TAIL
)
_ABSOLUTE_SYSTEM_PROMPT_COVERAGE = (
    _ABSOLUTE_SCORE_ANCHOR + _ABSOLUTE_COVERAGE_PRINCIPLE + _ABSOLUTE_JSON_TAIL
)


def _absolute_system_prompt(policy: VerbosityPolicy) -> str:
    if policy == "coverage":
        return _ABSOLUTE_SYSTEM_PROMPT_COVERAGE
    return _ABSOLUTE_SYSTEM_PROMPT


def _parse_score(content: str) -> tuple[float, str]:
    """从裁判输出抽 ``(score∈[0,5], rationale)``；非法 JSON 容错为 0 分（判负）。"""
    try:
        start = content.index("{")
        end = content.rindex("}")
        data = json.loads(content[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return 0.0, f"裁判输出无法解析为 JSON: {content[:120]!r}"
    try:
        score = float(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(5.0, score))
    return score, str(data.get("rationale", ""))


class LLMJudge:
    """绝对分裁判（实现 :class:`~agentcore.evals.types.Judge` 协议）—— L1 结果评分主轴。

    按 ``case.rubric`` 给被评答案 1–5 分，``passed = 均分 ≥ pass_threshold``（pass 阈值留在代码
    里、可审计可调，不让模型自报 pass）。``samples>1`` 时多采样取均分压裁判方差（思考模型不吃
    temperature，靠重采样降噪）。``provider`` 注入便于单测（脚本化假 provider 返回固定 JSON）。
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        *,
        pass_threshold: float = 4.0,
        samples: int = 1,
        scenario: str = "eval.judge.absolute",
    ) -> None:
        self._provider = provider
        self._model = model
        self._pass_threshold = pass_threshold
        self._samples = max(1, samples)
        self._scenario = scenario

    async def _one(
        self,
        rubric: str,
        user_message: str,
        answer: str,
        sources: str = "",
        *,
        policy: VerbosityPolicy = "concise",
    ) -> tuple[float, str]:
        user = (
            f"【评分准则】\n{rubric}\n\n"
            f"【任务】\n{user_message}\n\n"
            f"{sources}"
            f"【答案】\n{answer}\n\n"
            "请只输出 JSON。"
        )
        request = LLMRequest(
            messages=[
                LLMMessage(role="system", content=_absolute_system_prompt(policy)),
                LLMMessage(role="user", content=user),
            ],
            model=self._model,
            temperature=0.0,
            stream=False,
            scenario=self._scenario,
        )
        response = await self._provider.complete(request)
        return _parse_score(response.content or "")

    async def score(self, case: EvalCase, outcome: TurnOutcome) -> JudgeVerdict:
        scores: list[float] = []
        rationales: list[str] = []
        answer = judged_text(outcome)
        sources = _sources_section(outcome)
        policy = resolve_verbosity_policy(rubric=case.rubric, case_id=case.id)
        for _ in range(self._samples):
            s, r = await self._one(
                case.rubric or "", case.user_message, answer, sources, policy=policy
            )
            scores.append(s)
            rationales.append(r)
        avg = sum(scores) / len(scores) if scores else 0.0
        passed = avg >= self._pass_threshold
        if len(rationales) == 1:
            rationale = rationales[0]
        else:
            rationale = f"均分 {avg:.1f}/5（{len(scores)} 采样）：" + " || ".join(rationales)
        return JudgeVerdict(score=round(avg, 2), passed=passed, rationale=rationale)


def _eval_provider_and_model(mode: str) -> tuple[LLMProvider, str]:
    """构造接真实 DeepSeek 的裁判 (provider, model)：默认 Pro 档（Pro 评 Flash，压同家族自偏好）。

    裁判模型优先读 ``EVAL_JUDGE_MODEL``，否则回落 ``mode`` 档（默认 quality → Pro）的 chat 档；
    凭据复用 harness 的 eval 专用 key。绝对分裁判与 milestone 裁判共用本解析，避免「同一根因改两
    处」。函数内惰性 import 重依赖，保持本模块对 runtime/factory 零顶层耦合。
    """
    import os

    from agentcore.evals.eval_modes import resolve_profile_set
    from agentcore.evals.harness import _EVAL_CEILING, _eval_credentials
    from agentcore.llm.factory import build_provider

    provider = build_provider(_eval_credentials())
    model = os.environ.get("EVAL_JUDGE_MODEL", "").strip()
    if not model:
        profiles = resolve_profile_set(mode, custom_modes={}, ceiling=_EVAL_CEILING)
        model = profiles.model_for("chat")
    return provider, model


def build_default_judge(mode: str = "quality") -> LLMJudge:
    """构造接真实 DeepSeek 的绝对分裁判（Pro 评 Flash）。仅 CLI/nightly 真跑调用——单测注入假
    provider。模型/凭据解析见 :func:`_eval_provider_and_model`。"""
    provider, model = _eval_provider_and_model(mode)
    return LLMJudge(provider, model)


# --- L1 结果评分主轴：milestone 覆盖裁判 -------------------------------------

_MILESTONE_LEAD = (
    "你是严格的交付物评审。给定一个任务、一组『子目标(milestone)』和一份答案，逐条判断答案是否"
    "**确实覆盖**了每个子目标——只看交付内容，不看过程、不看谁写的、不看走了什么流程。\n"
)

_MILESTONE_CONCISE_PRINCIPLE = (
    "判定原则：真正满足才算覆盖；含糊带过、跑题、编造均不算覆盖；简洁正确优于冗长堆砌，绝不因"
    "答案更长更详细就判覆盖；惩罚注水与过度堆砌。\n"
)

_MILESTONE_COVERAGE_PRINCIPLE = (
    "判定原则：真正满足才算覆盖；含糊带过、跑题、编造、半成品与过程元话语均不算覆盖；"
    "覆盖与合成优先——不因答案实质更长更详细就拒判覆盖，也不因更短就自动判覆盖；惩罚注水。\n"
)

_MILESTONE_JSON_TAIL = (
    "对给定的每个 id 都要给出 true/false。只输出 JSON，不要其他文字：\n"
    '{"items": [{"id": "<子目标id>", "covered": true}], "rationale": "简短整体理由"}'
)

_MILESTONE_SYSTEM_PROMPT = _MILESTONE_LEAD + _MILESTONE_CONCISE_PRINCIPLE + _MILESTONE_JSON_TAIL
_MILESTONE_SYSTEM_PROMPT_COVERAGE = (
    _MILESTONE_LEAD + _MILESTONE_COVERAGE_PRINCIPLE + _MILESTONE_JSON_TAIL
)


def _milestone_system_prompt(policy: VerbosityPolicy) -> str:
    if policy == "coverage":
        return _MILESTONE_SYSTEM_PROMPT_COVERAGE
    return _MILESTONE_SYSTEM_PROMPT


def _parse_milestones(content: str, valid_ids: set[str]) -> tuple[dict[str, bool], str]:
    """从裁判输出抽 ``({id: covered}, rationale)``；非法 JSON → 空 dict（逐项保守按未覆盖计）。

    只采纳 ``valid_ids`` 内的 id（裁判幻觉出的多余 id 直接忽略），漏判的 id 由调用方按未覆盖兜底。
    """
    try:
        start = content.index("{")
        end = content.rindex("}")
        data = json.loads(content[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return {}, f"裁判输出无法解析为 JSON: {content[:120]!r}"
    covered: dict[str, bool] = {}
    for item in data.get("items", []) or []:
        if isinstance(item, dict) and str(item.get("id")) in valid_ids:
            covered[str(item["id"])] = bool(item.get("covered", False))
    return covered, str(data.get("rationale", ""))


class LLMMilestoneJudge:
    """milestone 覆盖裁判（实现 :class:`~agentcore.evals.types.MilestoneJudge` 协议）.

    一次调用让裁判对 ``case.milestones`` 逐条判 covered 布尔；覆盖率 = **加权命中比**，
    ``passed = 覆盖率 >= case.milestone_threshold``。漏判 / 解析失败的子目标**保守按未覆盖**计
    （绝不无据放过——这是结果断言，宁可判负也不放水）。``provider`` 注入便于单测。
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        *,
        scenario: str = "eval.judge.milestone",
    ) -> None:
        self._provider = provider
        self._model = model
        self._scenario = scenario

    async def score_milestones(self, case: EvalCase, outcome: TurnOutcome) -> MilestoneVerdict:
        milestones = case.milestones or []
        threshold = case.milestone_threshold
        if not milestones:
            return MilestoneVerdict(1.0, True, threshold, [], "无 milestone（平凡通过）")

        listing = "\n".join(
            f"- [{m['id']}] {m.get('desc', '')}（权重 {m.get('weight', 1)}）" for m in milestones
        )
        user = (
            f"【任务】\n{case.user_message}\n\n"
            f"【子目标清单】\n{listing}\n\n"
            f"{_sources_section(outcome)}"
            f"【答案】\n{judged_text(outcome)}\n\n"
            "请逐条判定每个子目标是否被覆盖，只输出 JSON。"
        )
        policy = resolve_verbosity_policy(rubric=case.rubric, case_id=case.id)
        request = LLMRequest(
            messages=[
                LLMMessage(role="system", content=_milestone_system_prompt(policy)),
                LLMMessage(role="user", content=user),
            ],
            model=self._model,
            temperature=0.0,
            stream=False,
            scenario=self._scenario,
        )
        response = await self._provider.complete(request)
        valid_ids = {str(m["id"]) for m in milestones}
        covered_map, rationale = _parse_milestones(response.content or "", valid_ids)

        items: list[MilestoneItemResult] = []
        total_w = 0.0
        hit_w = 0.0
        for m in milestones:
            mid = str(m["id"])
            try:
                weight = float(m.get("weight", 1) or 1)
            except (TypeError, ValueError):
                weight = 1.0
            covered = covered_map.get(mid, False)
            total_w += weight
            if covered:
                hit_w += weight
            items.append(
                MilestoneItemResult(
                    id=mid, desc=str(m.get("desc", "")), weight=weight, covered=covered
                )
            )
        coverage = hit_w / total_w if total_w else 0.0
        return MilestoneVerdict(
            coverage=round(coverage, 4),
            passed=coverage >= threshold,
            threshold=threshold,
            items=items,
            rationale=rationale,
        )


def build_default_milestone_judge(mode: str = "quality") -> LLMMilestoneJudge:
    """构造接真实 DeepSeek 的 milestone 覆盖裁判（Pro 评 Flash）。模型/凭据解析见
    :func:`_eval_provider_and_model`（与绝对分裁判同源）。仅 CLI/nightly 真跑调用。"""
    provider, model = _eval_provider_and_model(mode)
    return LLMMilestoneJudge(provider, model)


_PAIRWISE_LEAD = (
    "你是严格的成对评审。给定一个任务和两份答案（答案X、答案Y），依据评分准则判断哪份更好。\n"
)

_PAIRWISE_CONCISE_PRINCIPLE = (
    "重要原则：简洁正确优于冗长堆砌；不要因为答案更长、更啰嗦就偏向它；"
    "惩罚注水、冗余、重复与过度堆砌；只看是否真正满足准则。\n"
)

_PAIRWISE_COVERAGE_PRINCIPLE = (
    "重要原则：覆盖与合成优先——以任务要求的覆盖面、证据整合与可交付完整度为准；"
    "惩罚半成品、过程元话语（叙述讨论过程/下一步而无实质交付）、注水与重复堆砌；"
    "不要仅因答案更短就判其更好，也不要仅因答案更长就判其更好；"
    "实质内容更完整、合成更好的一方应胜出。\n"
)

_PAIRWISE_JSON_TAIL = (
    "先给一句简短理由，再给结论。只输出 JSON，不要其他文字：\n"
    '{"winner": "X" | "Y" | "tie", "rationale": "简短理由", "margin": 0}\n'
    "其中 margin 是优势强度 0–3（0=几乎打平，3=明显更好）。"
)

_SYSTEM_PROMPT = _PAIRWISE_LEAD + _PAIRWISE_CONCISE_PRINCIPLE + _PAIRWISE_JSON_TAIL
_SYSTEM_PROMPT_COVERAGE = _PAIRWISE_LEAD + _PAIRWISE_COVERAGE_PRINCIPLE + _PAIRWISE_JSON_TAIL


def _pairwise_system_prompt(policy: VerbosityPolicy) -> str:
    if policy == "coverage":
        return _SYSTEM_PROMPT_COVERAGE
    return _SYSTEM_PROMPT


def _parse_pairwise(content: str) -> tuple[str, str, int]:
    """从裁判输出抽 ``(winner∈{X,Y,tie}, rationale, margin)``；非法 JSON 容错为 tie。"""
    try:
        start = content.index("{")
        end = content.rindex("}")
        data = json.loads(content[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return "tie", f"裁判输出无法解析为 JSON: {content[:120]!r}", 0
    raw = str(data.get("winner", "")).strip().upper()
    winner = "X" if raw == "X" else "Y" if raw == "Y" else "tie"
    rationale = str(data.get("rationale", ""))
    try:
        margin = max(0, min(3, int(data.get("margin", 0))))
    except (TypeError, ValueError):
        margin = 0
    return winner, rationale, margin


class LLMPairwiseJudge:
    """成对偏好裁判（实现 :class:`~agentcore.evals.types.PairwiseJudge` 协议）。

    ``swap=True`` 时对每对答案正反各判一次，**仅当两序判给同一臂才计该臂胜**，否则记 tie
    ——这把「位置偏见」（裁判总偏向某个位置）直接抵消掉。``provider`` 注入便于单测。
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        *,
        swap: bool = True,
        scenario: str = "eval.judge.pairwise",
    ) -> None:
        self._provider = provider
        self._model = model
        self._swap = swap
        self._scenario = scenario

    async def _one(
        self,
        rubric: str,
        user_message: str,
        x: str,
        y: str,
        *,
        policy: VerbosityPolicy = "concise",
    ) -> tuple[str, str, int]:
        """单方向判一次（X / Y 为位置标签，与臂无关）。"""
        user = (
            f"【评分准则】\n{rubric}\n\n"
            f"【任务】\n{user_message}\n\n"
            f"【答案X】\n{x}\n\n"
            f"【答案Y】\n{y}\n\n"
            "请只输出 JSON。"
        )
        request = LLMRequest(
            messages=[
                LLMMessage(role="system", content=_pairwise_system_prompt(policy)),
                LLMMessage(role="user", content=user),
            ],
            model=self._model,
            temperature=0.0,
            stream=False,
            scenario=self._scenario,
        )
        response = await self._provider.complete(request)
        return _parse_pairwise(response.content or "")

    async def compare(
        self,
        *,
        rubric: str,
        user_message: str,
        subject_arm: str,
        subject_content: str,
        baseline_arm: str,
        baseline_content: str,
        archetype: str | None = None,
        case_id: str | None = None,
    ) -> PairwiseVerdict:
        policy = resolve_verbosity_policy(
            archetype=archetype, rubric=rubric, case_id=case_id
        )
        # 正序：X=主臂、Y=基准臂
        w1, r1, m1 = await self._one(
            rubric, user_message, subject_content, baseline_content, policy=policy
        )
        if not self._swap:
            winner = subject_arm if w1 == "X" else baseline_arm if w1 == "Y" else "tie"
            return PairwiseVerdict(winner=winner, rationale=r1, margin=m1)

        # 反序：X=基准臂、Y=主臂
        w2, r2, m2 = await self._one(
            rubric, user_message, baseline_content, subject_content, policy=policy
        )
        a1 = subject_arm if w1 == "X" else baseline_arm if w1 == "Y" else "tie"
        a2 = baseline_arm if w2 == "X" else subject_arm if w2 == "Y" else "tie"

        # 仅当两序判给同一臂才计胜，否则（含位置翻转 / 任一 tie）记 tie
        winner = a1 if (a1 != "tie" and a1 == a2) else "tie"
        rationale = f"[正序] {r1} || [反序] {r2}"
        return PairwiseVerdict(winner=winner, rationale=rationale, margin=max(m1, m2))


class EnsemblePairwiseJudge:
    """多评委成对裁判：实现同一 :class:`~agentcore.evals.types.PairwiseJudge` 协议。

    内含 N 个子裁判（可不同 model / 不同呈现顺序）。聚合规则：

    - **多数**决定 ``winner``（票数 > N/2 的臂名胜；``tie`` 票不计入任一臂）；
    - ``margin`` 取各子裁判 margin 的**中位数**；
    - **无多数**（如 1:1:1、平票、或仅有分散票）→ ``winner="tie"``，rationale 标注分歧票型。

    成本语义：若子裁判是带 ``swap=True`` 的 :class:`LLMPairwiseJudge`，则每次
    ``compare`` 至多触发 **2N** 次 LLM（N 评委 × 正反序）——这是接受的叠乘成本。
    """

    def __init__(
        self,
        judges: list[PairwiseJudge],
        *,
        judge_ids: list[str] | None = None,
    ) -> None:
        if not judges:
            raise ValueError("EnsemblePairwiseJudge 至少需要 1 个子裁判")
        self._judges = list(judges)
        if judge_ids is None:
            self._ids = [f"j{i}" for i in range(len(self._judges))]
        else:
            if len(judge_ids) != len(self._judges):
                raise ValueError("judge_ids 长度须与 judges 一致")
            self._ids = list(judge_ids)

    async def compare(
        self,
        *,
        rubric: str,
        user_message: str,
        subject_arm: str,
        subject_content: str,
        baseline_arm: str,
        baseline_content: str,
        archetype: str | None = None,
        case_id: str | None = None,
    ) -> PairwiseVerdict:
        votes: list[JudgeVote] = []
        for jid, judge in zip(self._ids, self._judges, strict=True):
            v = await judge.compare(
                rubric=rubric,
                user_message=user_message,
                subject_arm=subject_arm,
                subject_content=subject_content,
                baseline_arm=baseline_arm,
                baseline_content=baseline_content,
                archetype=archetype,
                case_id=case_id,
            )
            votes.append(JudgeVote(judge_id=jid, winner=v.winner, margin=v.margin))

        tallies: dict[str, int] = {}
        for vote in votes:
            if vote.winner == "tie":
                continue
            tallies[vote.winner] = tallies.get(vote.winner, 0) + 1

        n = len(votes)
        need = n // 2 + 1  # 严格多数
        winner = "tie"
        for arm, count in tallies.items():
            if count >= need:
                winner = arm
                break

        margins = [v.margin for v in votes]
        margin = int(statistics.median(margins)) if margins else 0

        tally_bits = [f"{v.judge_id}={v.winner}(m{v.margin})" for v in votes]
        vote_summary = ", ".join(tally_bits)
        if winner == "tie":
            rationale = f"无多数（分歧票型: {vote_summary}）"
        else:
            rationale = f"多数={winner}（票型: {vote_summary}）"
        return PairwiseVerdict(winner=winner, rationale=rationale, margin=margin, votes=votes)
