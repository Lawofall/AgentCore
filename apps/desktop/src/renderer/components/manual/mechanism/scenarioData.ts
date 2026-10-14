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
  /** 进阶形态：默认折进「更多形态」；常用四式（并行 / 串行 / 正反辩论 / 再开一层）常驻。 */
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

const edge = (
  source: string,
  target: string,
  kind: "dep" | "delegate" | "continuation" = "dep",
): GraphEdge => ({ id: `${source}->${target}`, source, target, kind });

export const SCENARIOS: Scenario[] = [
  {
    title: "多人同时开工",
    desc: "三段没有先后，同一批一起开。能不能同时做，看的是有没有先后，不是另开一种模式。",
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
    ],
    edges: [
      edge("__input__", "w1"),
      edge("__input__", "w2"),
      edge("__input__", "w3"),
    ],
  },
  {
    title: "一条线按顺序推进",
    layout: "tree",
    desc: "调研 → 分析 → 撰写：下一段等上一段封口再开；上游结论交给下一段。树形布局自上而下读更顺。",
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
    ],
    edges: [
      edge("__input__", "s1"),
      edge("s1", "s2"),
      edge("s2", "s3"),
    ],
  },
  {
    title: "正反辩论",
    desc: "正方与反方对垒，图上左右分带。辩论单独一张图。",
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
    ],
    edges: [
      edge("__input__", "pro"),
      edge("__input__", "con"),
    ],
  },
  {
    title: "再开一层",
    desc: "一段再开下一层（虚线）。下一层有段失败会标红，不拖垮已经封口的段。结论回到对着你的这扇窗。",
    nodes: [
      input("实现一个新设置页，前端 + 测试分工完成"),
      agent("pm", "项目经理", "completed", {
        task: "再拆成下一段",
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
    ],
    edges: [
      edge("__input__", "pm"),
      edge("pm", "fe", "delegate"),
      edge("pm", "qa", "delegate"),
    ],
  },
  {
    title: "执行中的样子",
    advanced: true,
    desc: "有的段正在写（蓝环 + 流式预览）、有的已封口、有的还在等。",
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
    ],
    edges: [
      edge("__input__", "r1"),
      edge("__input__", "r2"),
      edge("r2", "r3"),
    ],
  },
  {
    title: "再往下拆一层",
    advanced: true,
    desc: "一段还可以再开下一层。层数有上限，不会无限拆下去；下一层沉在这一段下面。",
    nodes: [
      input("拆解并实现协作图，前端再往下拆一层"),
      agent("mpm", "项目经理", "completed", {
        task: "先拆成前端这一段",
        durationMs: 9000,
        toolCount: 1,
      }),
      agent("lead", "前端组长", "completed", {
        task: "再拆前端工作，交给下一段",
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
    ],
    edges: [
      edge("__input__", "mpm"),
      edge("mpm", "lead", "delegate"),
      edge("lead", "eng1", "delegate"),
      edge("lead", "eng2", "delegate"),
    ],
  },
  {
    title: "接着做",
    advanced: true,
    desc: "再开一段，接在已经看见的结论上接着改。图上可以挂「续 ×N」。不是从零重来。",
    nodes: [
      input("把上一版报告的第 2 章重写得更详细"),
      agent("orig", "撰写员", "completed", {
        task: "撰写报告初稿",
        revisionSummary: "重写第 2 章并扩充论据",
        durationMs: 10200,
        toolCount: 3,
        isRevision: true,
        continuationIndex: 1,
      }),
    ],
    edges: [edge("__input__", "orig")],
  },
  {
    title: "多段同时开",
    advanced: true,
    desc: "九段同时润色：横向铺满、纵向超出内嵌高度时顶对齐 + 底部渐隐，提示「还有更多」。看全图点右上角的「图」。",
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
    ],
    edges: ["b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9"].map((id) =>
      edge("__input__", id),
    ),
  },
];
