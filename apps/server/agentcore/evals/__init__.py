"""AgentCore 评估体系（eval harness）.

把黄金任务集喂给真实运行路径（``react_loop`` / ``run_chat_pipeline``），用确定性
断言 + LLM 裁判双信号判定，产出可与 baseline 对比、可挂 CI 的回归报告。

现状见 ``docs/02-架构/后端架构.md`` §五；真跑评测余项见路线图摘要（详细提案不在公开仓）。
本包不被服务代码引用，纯离线工具。

P0–P1（均已落地）：types + harness + 确定性 Check + seed_lint + runner + report + CLI +
LLMJudge（语义打分）+ milestone 覆盖裁判 + 成对裁判（团队 vs 单体）+ 相对基线观测
（``observe.py``，翻转方向区分方差/单方向变差，不当硬门）+ kappa 校准回路 + MAST 标签聚合
+ CI nightly（evals-nightly.yml）。
裁判被评口径 = ``judged_text``（聊天 ``content`` + 终版 ``artifacts``，末次 file_write）；
多评委可选 ``EnsemblePairwiseJudge``（``EVAL_JUDGE_ENSEMBLE``）。
P2+（待落，皆非代码缺口）：L2/L3 真模型出数（需 EVAL_DEEPSEEK_API_KEY + 预算）+
gold-set 人工核验（kappa 门，cases/gold/labels.json 现 100 条：30 human + 70 rubric_derived）。

本 ``__init__`` 只暴露**纯静态**部分（types + checks 注册表 + seed_lint）——故意不在此
import ``harness`` / ``runner`` / ``report``，让 ``seed_lint`` 这类零 LLM 静态校验
（per-PR 硬门禁）的 import 路径不被 runtime（pipeline/engine）拖下水。需要跑用例时
显式 ``from agentcore.evals.harness import EvalHarness`` / ``...runner import run_suite``。
"""

from agentcore.evals.checks import CHECK_NAMES, build_check
from agentcore.evals.mast import (
    MAST_CODES,
    MAST_GROUPS,
    MAST_MODES,
    MastMode,
    group_of,
    is_valid_mast_code,
    label_of,
)
from agentcore.evals.seed_lint import (
    lint_case,
    lint_comparison_case,
    lint_comparison_suite,
    lint_suite,
)
from agentcore.evals.types import (
    ArmResult,
    CaseReport,
    Check,
    CheckOutcome,
    ComparisonCase,
    ComparisonCaseReport,
    ComparisonReport,
    EvalCase,
    EvalConfigError,
    EvalReport,
    Harness,
    Judge,
    JudgeVerdict,
    JudgeVote,
    MilestoneItemResult,
    MilestoneJudge,
    MilestoneVerdict,
    PairwiseJudge,
    PairwiseVerdict,
    TurnOutcome,
    artifacts_from_tool_calls,
    judged_text,
)

__all__ = [
    "CHECK_NAMES",
    "MAST_CODES",
    "MAST_GROUPS",
    "MAST_MODES",
    "ArmResult",
    "CaseReport",
    "Check",
    "CheckOutcome",
    "ComparisonCase",
    "ComparisonCaseReport",
    "ComparisonReport",
    "EvalCase",
    "EvalConfigError",
    "EvalReport",
    "Harness",
    "Judge",
    "JudgeVerdict",
    "JudgeVote",
    "MastMode",
    "MilestoneItemResult",
    "MilestoneJudge",
    "MilestoneVerdict",
    "PairwiseJudge",
    "PairwiseVerdict",
    "TurnOutcome",
    "artifacts_from_tool_calls",
    "build_check",
    "group_of",
    "is_valid_mast_code",
    "judged_text",
    "label_of",
    "lint_case",
    "lint_comparison_case",
    "lint_comparison_suite",
    "lint_suite",
]
