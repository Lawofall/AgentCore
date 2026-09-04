import type {
  DebateModel,
  DebateRoundModel,
} from "@/components/chat/debate/model";
import type { Execution, RunNode } from "@/stores/execution";

const PRO_COLOR = "var(--debate-side-pro)";
const CON_COLOR = "var(--debate-side-con)";

function roundSide(
  sideKey: "pro" | "con",
  name: string,
  runId: string,
  model: string,
): DebateRoundModel["sides"][number] {
  return {
    key: runId,
    sideKey,
    name,
    stance: sideKey,
    colorVar: sideKey === "pro" ? PRO_COLOR : CON_COLOR,
    model,
    run: null,
  };
}

function score(
  sideKey: "pro" | "con",
  name: string,
  argument: number,
  engagement: number,
  evidence: number,
  penalties: string[],
  total: number,
): DebateRoundModel["scores"][number] {
  return {
    sideKey,
    name,
    colorVar: sideKey === "pro" ? PRO_COLOR : CON_COLOR,
    argument,
    engagement,
    evidence,
    penalties,
    note: "",
    total,
  };
}

const DEMO_ROUNDS: DebateRoundModel[] = [
  {
    roundNo: 1,
    focus: "成本与交付节奏",
    summary: "正方强调试点 ROI；反方质疑隐藏运维成本。",
    verdict: {
      real_clash: true,
      new_arguments: true,
      converged: false,
      stop_reason: "",
      rationale: "成本口径尚未对齐",
    },
    sides: [
      roundSide("pro", "加速派", "run_pro_1", "deepseek/deepseek-v4-flash"),
      roundSide("con", "审慎派", "run_con_1", "deepseek/deepseek-v4-flash"),
    ],
    clashes: [],
    inFlight: false,
    userInterjections: [],
    crossExam: [],
    witnessExam: [],
    findings: [],
    threadTurns: [],
    scores: [
      score("pro", "加速派", 4, 3, 4, [], 11),
      score("con", "审慎派", 3, 4, 3, [], 10),
    ],
  },
  {
    roundNo: 2,
    focus: "风险与回滚",
    summary: "质询后反方承认可分阶段放行；正方承诺熔断。",
    verdict: {
      real_clash: true,
      new_arguments: false,
      converged: true,
      stop_reason: "converged",
      rationale: "双方接受分阶段试点",
    },
    sides: [
      roundSide("pro", "加速派", "run_pro_2", "deepseek/deepseek-v4-flash"),
      roundSide("con", "审慎派", "run_con_2", "deepseek/deepseek-v4-flash"),
    ],
    clashes: [],
    inFlight: false,
    userInterjections: [],
    crossExam: [],
    witnessExam: [],
    findings: [],
    threadTurns: [],
    scores: [
      score("pro", "加速派", 4, 4, 3, [], 11),
      score("con", "审慎派", 3, 3, 4, ["把未证实的尾部风险说成既定事实"], 9),
    ],
  },
];

/** 手册嵌入共用的已收场正反对垒演示数据（记分牌 / 终审舞台）。 */
export const DEMO_DEBATE_MESSAGE_ID = "manual-embed-debate";

export const DEMO_DEBATE_MODEL: DebateModel = {
  form: "debate",
  motion: "是否先做云端试点，再扩本地引擎？",
  stopReason: "converged",
  moderatorRunId: "moderator",
  moderatorModel: null,
  moderatorOrigin: null,
  sameModelDebate: false,
  narrativeFirst: true,
  crossExamEnabled: false,
  evidenceLedger: [],
  subtopics: null,
  rounds: DEMO_ROUNDS,
  brief: {
    leaning:
      "倾向加速派：可以先做云端试点。若合规成本核实后不可接受，则翻向审慎派。",
    confidence: "high",
    decisive: "分阶段试点能同时控成本与验证价值",
    crux: "试点范围与回滚门槛",
    recommendation: "",
    strongest_points: {
      pro: "ROI 路径清晰，队员可两周交付",
      con: "运维与合规成本仍需写清",
    },
    handoffs: [
      { kind: "value", text: "要不要牺牲速度换更稳的回滚" },
      { kind: "fact", text: "试点实际成本" },
      { kind: "question", text: "第一批放行哪些试点" },
    ],
  },
  sides: [
    {
      key: "pro",
      name: "加速派",
      stance: "pro",
      model: undefined,
      is_subject: false,
    },
    {
      key: "con",
      name: "审慎派",
      stance: "con",
      model: undefined,
      is_subject: false,
    },
  ],
  closings: [],
  opening: "这场请双方围绕试点节奏交锋；CEO 只收简报，最终由你拍板。",
  settled: true,
};

function moderatorRun(): RunNode {
  return {
    id: "moderator",
    agentId: "moderator",
    status: "completed",
    kind: "agent",
    model: "deepseek/deepseek-v4-flash",
    parentRunId: null,
    continuesRunId: null,
    receivedContext: [],
  } as unknown as RunNode;
}

export const DEMO_DEBATE_EXECUTION: Execution = {
  status: "completed",
  runs: [moderatorRun()],
  agents: [],
  frames: [],
  debate: null,
  debateRounds: [],
} as unknown as Execution;
