import type { AskCommenceScene } from "./askCommenceMock";

/** Preview-only scenes — ask 开场布局已退役；深链 `#/preview/ask-commence?s=<id>`。 */
export const ASK_COMMENCE_SCENES: AskCommenceScene[] = [
  {
    id: "ask-commence-v1",
    title: "Compact Decision",
    intent:
      "【已退役】决策优先：压缩说明、选项占主视觉，主/次 CTA 固定底栏——类似 Linear issue confirm。",
    paradigm: "Linear",
  },
  {
    id: "ask-commence-v2",
    title: "Brief + Choose",
    intent:
      "【已退役】题干与选项常驻（紧凑单行选项）；brief/起步计划折叠；人话常驻。风格 pills 常驻一行。",
    paradigm: "Notion AI × Executive Summary",
  },
  {
    id: "ask-commence-v3",
    title: "Wizard Step",
    intent:
      "【已退役】一题一答：当前题绝对焦点、大选项卡；进度克制，计划沉为次要 chips。",
    paradigm: "Structured wizard",
  },
  {
    id: "ask-commence-v4",
    title: "Executive Summary",
    intent:
      "【已退役】顶部一行结论 + 关键参数 pill，下方精简选项列表——Cursor / ChatGPT 确认条升级版。",
    paradigm: "Cursor / ChatGPT",
  },
  {
    id: "ask-commence-v5",
    title: "Generic Clarify",
    intent:
      "【现生产】通用澄清卡 AskDecisionBody：无开工提案仪式；多题编号跳转、一次提交；wire 空 style/format 不渲染场面区。",
    paradigm: "通用 ask_user",
  },
];
