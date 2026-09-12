import type { RunStatus } from "@/stores/execution";
import type { GraphEdge } from "@/stores/graph";
import type { GraphLayout } from "@agentcore/graph-layout";
export interface PreviewNode {
  id: string;
  type: "agent" | "userInput" | "captain";
  data: Record<string, unknown>;
}

export interface Scenario {
  title: string;
  desc: string;
  /** ELK 布局；缺省走左右流（与产品默认一致）。串行链用 "tree" 自上而下读。 */
  layout?: GraphLayout;
  /** 进阶形态：默认折进「更多形态」；常用四式（并行 / 串行 / 正反辩论 / 嵌套小队）常驻。 */
  advanced?: boolean;
  nodes: PreviewNode[];
  edges: GraphEdge[];
}

/** worker 节点：默认补齐 AgentNodeData 必填项，`extra` 覆盖差异化字段。 */
const agent = (
  id: string,
  role: string,
  status: RunStatus,
  extra: Record<string, unknown> = {},
): PreviewNode => ({
  id,
  type: "agent",
  data: {
    agentId: id,
    runId: id,
    role,
    status,
    isAnimating: status === "running",
    task: "",
    outputPreview: "",
    tokenCount: 0,
    toolCount: 0,
    focused: false,
    ...extra,
  },
});

const input = (label: string): PreviewNode => ({
  id: "__input__",
  type: "userInput",
  data: { variant: "input", status: "completed", label },
});

const captain = (id: string, status: RunStatus, preview = ""): PreviewNode => ({
  id,
  type: "captain",
  data: { variant: "captain", status, label: "", preview },
});

const edge = (
  source: string,
  target: string,
  kind: "dep" | "delegate" | "continuation" = "dep",
): GraphEdge => ({ id: `${source}->${target}`, source, target, kind });

export const SCENARIOS: Scenario[] = [
  {
    title: "多人同时开工",
    desc: "三位队员没有先后依赖，同一批一起开干；全部完成后 CEO 汇总收口。能不能并行，看的是分工有没有先后，不是另开一种模式。",
    nodes: [
      input("把这个需求拆成三块并行做"),
      agent("w1", "文件操作员", "completed", {
        task: "创建 greeting.txt 并写入测试输出",
        durationMs: 4200,
        toolCount: 3,
      }),
      agent("w2", "文件操作员", "completed", {
        task: "创建测试笔记文件并记录测试过程",
        durationMs: 5100,
        toolCount: 2,
      }),
      agent("w3", "脚本编写员", "completed", {
        task: "编写多行打印脚本并验证可运行",
        durationMs: 6800,
        toolCount: 5,
      }),
      captain(
        "cap",
        "completed",
        "三个文件都已在工作区根目录创建完成，均可直接查看与运行。",
      ),
    ],
    edges: [
      edge("__input__", "w1"),
      edge("__input__", "w2"),
      edge("__input__", "w3"),
      edge("w1", "cap"),
      edge("w2", "cap"),
      edge("w3", "cap"),
    ],
  },
  {
    title: "一条线按顺序推进",
    layout: "tree",
    desc: "调研 → 分析 → 撰写：每个人都等上一位交活再开工；上游结论会交给下游接着用。树形布局自上而下读更顺。",
    nodes: [
      input("调研近 7 日成本趋势并产出一段摘要"),
      agent("s1", "调研员", "completed", {
        task: "检索成本相关数据源与口径",
        durationMs: 3400,
        toolCount: 2,
      }),
      agent("s2", "数据分析师", "completed", {
        task: "汇总近 7 日成本趋势、定位异常点",
        durationMs: 5200,
        toolCount: 1,
      }),
      agent("s3", "文案", "completed", {
        task: "据分析结论撰写一段可读摘要",
        durationMs: 2600,
      }),
      captain("scap", "completed", "成本趋势摘要已生成，关键异常已标注。"),
    ],
    edges: [
      edge("__input__", "s1"),
      edge("s1", "s2"),
      edge("s2", "s3"),
      edge("s3", "scap"),
    ],
  },
  {
    title: "正反辩论",
    desc: "辩论三形态之一：正方与反方对垒，图上左右分带，最后汇到 CEO 裁决。另外两种——红队挑刺、多方圆桌——在「更多形态」里。",
    nodes: [
      input("评估是否采用激进重构方案"),
      agent("pro", "架构师", "completed", {
        stance: "pro",
        group: "debate:debate",
        task: "论证采用激进重构方案的收益与可行性",
        durationMs: 5000,
      }),
      agent("con", "架构师", "completed", {
        stance: "con",
        group: "debate:debate",
        task: "论证保持稳健迭代、反对激进重构的理由",
        durationMs: 4800,
      }),
      captain(
        "cap3",
        "completed",
        "综合正反双方论点，建议分阶段推进：先在隔离分支验证关键风险，再决定是否全量切换。",
      ),
    ],
    edges: [
      edge("__input__", "pro"),
      edge("__input__", "con"),
      edge("pro", "cap3"),
      edge("con", "cap3"),
    ],
  },
  {
    title: "嵌套小队",
    desc: "项目经理再带一支小队（虚线委派）。子队员失败会标红，但不拖垮整队——CEO 仍可汇总已完成的部分。",
    nodes: [
      input("实现一个新设置页，前端 + 测试分工完成"),
      agent("pm", "项目经理", "completed", {
        task: "拆解任务并协调子团队",
        durationMs: 8000,
        toolCount: 1,
      }),
      agent("fe", "前端开发", "completed", {
        task: "实现页面组件与样式",
        durationMs: 6000,
        toolCount: 4,
        isSubtask: true,
      }),
      agent("qa", "测试", "failed", {
        task: "为新页面编写单元测试",
        isSubtask: true,
      }),
      captain(
        "cap4",
        "completed",
        "页面主体已完成并通过自测；测试子任务失败，待修复后补单测。",
      ),
    ],
    edges: [
      edge("__input__", "pm"),
      edge("pm", "fe", "delegate"),
      edge("pm", "qa", "delegate"),
      edge("pm", "cap4"),
    ],
  },
  {
    title: "执行中的样子",
    advanced: true,
    desc: "有人正在写（蓝环 + 流式预览）、有人已交活、有人还在等；深度思考的队员会带「深度」徽章。",
    nodes: [
      input("分析近 7 日成本趋势并产出一段摘要"),
      agent("r1", "调研员", "running", {
        task: "检索最佳实践",
        outputPreview:
          "正在检索 React Flow 自定义节点的最佳实践，已找到 3 篇相关文档，正在归纳关键结论",
        toolCount: 2,
      }),
      agent("r2", "数据分析师", "completed", {
        task: "汇总近 7 日成本趋势数据",
        durationMs: 3200,
        toolCount: 1,
      }),
      agent("r3", "文案", "pending", {
        task: "根据分析结论撰写一段摘要",
      }),
      captain("cap2", "pending", ""),
    ],
    edges: [
      edge("__input__", "r1"),
      edge("__input__", "r2"),
      edge("__input__", "r3"),
      edge("r1", "cap2"),
      edge("r2", "cap2"),
      edge("r3", "cap2"),
    ],
  },
  {
    title: "红队挑刺",
    advanced: true,
    desc: "辩论三形态之二：方案方被审，红队专找风险与漏洞；挖尽或修补到位后收口，侧重风险清单与加固建议。",
    nodes: [
      input("压力测试这个上线方案，把隐患挖出来"),
      agent("scheme", "方案方", "completed", {
        group: "debate:red_team",
        task: "陈述上线方案与已做防护",
        durationMs: 4200,
      }),
      agent("rt1", "红队·安全", "completed", {
        group: "debate:red_team",
        task: "攻击权限边界与数据泄露面",
        durationMs: 5100,
      }),
      agent("rt2", "红队·合规", "completed", {
        group: "debate:red_team",
        task: "挑刺审计留痕与用户告知义务",
        durationMs: 4600,
      }),
      captain(
        "rtcap",
        "completed",
        "共挖出 4 项高优风险；方案方已回应修补路径，建议上线前先关权限与告知两项。",
      ),
    ],
    edges: [
      edge("__input__", "scheme"),
      edge("__input__", "rt1"),
      edge("__input__", "rt2"),
      edge("scheme", "rtcap"),
      edge("rt1", "rtcap"),
      edge("rt2", "rtcap"),
    ],
  },
  {
    title: "多方圆桌",
    advanced: true,
    desc: "辩论三形态之三：三个以上视角多边碰撞，把观点光谱铺开；适合探讨与学习，过程地图往往比单一裁决更有用。",
    nodes: [
      input("从产品、工程、运营三个视角讨论远程办公政策"),
      agent("p1", "产品视角", "completed", {
        group: "debate:roundtable",
        task: "从用户体验与节奏谈利弊",
        durationMs: 3800,
      }),
      agent("p2", "工程视角", "completed", {
        group: "debate:roundtable",
        task: "从协作成本与交付质量谈利弊",
        durationMs: 4100,
      }),
      agent("p3", "运营视角", "completed", {
        group: "debate:roundtable",
        task: "从人效与合规谈利弊",
        durationMs: 3900,
      }),
      captain(
        "rtbcap",
        "completed",
        "三视角光谱已铺开：核心分歧在「同步密度」与「办公日门槛」，交给你拍板。",
      ),
    ],
    edges: [
      edge("__input__", "p1"),
      edge("__input__", "p2"),
      edge("__input__", "p3"),
      edge("p1", "rtbcap"),
      edge("p2", "rtbcap"),
      edge("p3", "rtbcap"),
    ],
  },
  {
    title: "多层小队",
    advanced: true,
    desc: "CEO → 项目经理 → 前端组长 → 工程师：小队还能再带一层。嵌套有硬上限，不会无限拆下去；整条子树沉在主干线下方。",
    nodes: [
      input("拆解并实现协作图，前端再分一层小队"),
      agent("mpm", "项目经理", "completed", {
        task: "拆解任务、协调前端小队",
        durationMs: 9000,
        toolCount: 1,
      }),
      agent("lead", "前端组长", "completed", {
        task: "细分前端工作并分派给队员",
        durationMs: 6500,
        toolCount: 2,
        isSubtask: true,
      }),
      agent("eng1", "前端工程师", "completed", {
        task: "实现预览页布局与卡片样式",
        durationMs: 5200,
        toolCount: 4,
        isSubtask: true,
      }),
      agent("eng2", "前端工程师", "running", {
        task: "接入真实布局并联调",
        outputPreview: "正在把适应宽度接到内嵌画布，已联通 2/3…",
        toolCount: 2,
        isSubtask: true,
      }),
      captain("mcap", "pending", ""),
    ],
    edges: [
      edge("__input__", "mpm"),
      edge("mpm", "lead", "delegate"),
      edge("lead", "eng1", "delegate"),
      edge("lead", "eng2", "delegate"),
      edge("mpm", "mcap"),
    ],
  },
  {
    title: "带现场续派",
    advanced: true,
    desc: "CEO 唤回刚干完的同一位队员，带着上次的现场接着改——图上挂一条点线「续 ×N」。是同一个人的下一次产出，不是新队员；现场对不上会明确拒绝，不会悄悄换人。",
    nodes: [
      input("把上一版报告的第 2 章重写得更详细"),
      agent("orig", "撰写员", "completed", {
        task: "撰写报告初稿",
        durationMs: 6400,
        toolCount: 2,
      }),
      agent("rev", "撰写员", "completed", {
        task: "撰写报告初稿",
        revisionSummary: "重写第 2 章并扩充论据",
        durationMs: 3800,
        toolCount: 1,
        isRevision: true,
        continuationIndex: 1,
      }),
      captain("rcap", "completed", "已交付重写后的第 2 章，其余章节沿用初稿。"),
    ],
    edges: [
      edge("__input__", "orig"),
      edge("orig", "rcap"),
      edge("orig", "rev", "continuation"),
    ],
  },
  {
    title: "大团队并行",
    advanced: true,
    desc: "九路同时润色：横向铺满、纵向超出内嵌高度时顶对齐 + 底部渐隐，提示「还有更多」——看全图请进全屏。也用来感受小缩放下节点是否仍可读。",
    nodes: [
      input("把这份长报告拆成 9 块并行润色"),
      agent("b1", "润色员", "completed", {
        task: "润色第 1 章：引言",
        durationMs: 2600,
        toolCount: 1,
      }),
      agent("b2", "润色员", "completed", {
        task: "润色第 2 章：背景",
        durationMs: 3100,
        toolCount: 1,
      }),
      agent("b3", "润色员", "completed", {
        task: "润色第 3 章：方法",
        durationMs: 4200,
        toolCount: 2,
      }),
      agent("b4", "润色员", "completed", {
        task: "润色第 4 章：实验",
        durationMs: 3800,
        toolCount: 1,
      }),
      agent("b5", "润色员", "completed", {
        task: "润色第 5 章：结果",
        durationMs: 2900,
        toolCount: 1,
      }),
      agent("b6", "润色员", "completed", {
        task: "润色第 6 章：讨论",
        durationMs: 3300,
        toolCount: 2,
      }),
      agent("b7", "润色员", "running", {
        task: "润色第 7 章：相关工作",
        outputPreview: "正在统一术语与时态，已处理 12 处表述，剩余约 1/3…",
        toolCount: 1,
      }),
      agent("b8", "润色员", "pending", {
        task: "润色第 8 章：局限性",
      }),
      agent("b9", "校对员", "failed", {
        task: "全文交叉引用与编号校对",
      }),
      captain("bcap", "pending", ""),
    ],
    edges: ["b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9"].flatMap(
      (id) => [edge("__input__", id), edge(id, "bcap")],
    ),
  },
];
