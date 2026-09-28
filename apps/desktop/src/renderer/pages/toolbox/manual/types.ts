/**
 * 产品手册结构化内容源类型。
 *
 * 约束：纯数据、可序列化（无 JSX / 无函数）。图标用 lucide 名称字符串，
 * 真组件嵌入用 embed key 字符串，由渲染器查注册表。
 */

/** 行内富文本片段——可序列化，覆盖粗体 / 路由跳转 / 页内锚点。 */
export type InlineSpan =
  | string
  | { text: string; strong?: boolean }
  | { text: string; link: { kind: "go" | "jump"; to: string } };

export type RichText = string | InlineSpan[];

export type CalloutVariant = "tip" | "info" | "warning";

export type ManualBlock =
  | { type: "lead"; text: RichText }
  | { type: "paragraph"; text: RichText; emphasis?: boolean }
  | { type: "callout"; variant: CalloutVariant; text: RichText }
  | {
      type: "cards";
      cols?: 2 | 3;
      items: {
        title: string;
        desc: string;
        icon?: string;
        highlight?: boolean;
      }[];
    }
  | { type: "bullets"; items: { title: string; desc: RichText }[] }
  | { type: "steps"; items: { title: string; desc: RichText }[] }
  | {
      type: "doDont";
      good: { label?: string; items: string[] };
      bad: { label?: string; items: string[] };
    }
  | {
      type: "faq";
      items: {
        q: string;
        /** 答案正文；可混嵌 boundaryTable 等结构化片段 */
        a: FaqAnswerPart[];
      }[];
    }
  | {
      type: "boundaryTable";
      rows: { can: string; approve: string; wont: string }[];
    }
  | {
      type: "settingsRows";
      rows: { label: string; desc: string; to: string }[];
    }
  | { type: "embed"; key: string };

/** FAQ 答案片段：纯富文本，或内嵌边界表。 */
export type FaqAnswerPart =
  | { type: "text"; text: RichText }
  | {
      type: "boundaryTable";
      rows: { can: string; approve: string; wont: string }[];
    };

export type ManualSurface = "desktop" | "web" | "mobile";

/** default = CEO 可查事实；optional = 选读；off = 只留用户手册，不进 CEO 语料。 */
export type ManualAiPlacement = "default" | "optional" | "off";

export interface ManualSection {
  id: string;
  title: string;
  /** lucide-react 导出名，如 "Compass" */
  icon: string;
  blocks: ManualBlock[];
  /** 缺省 = 三端都有。缺席的端：CEO 先说无此入口。 */
  availability?: ManualSurface[];
  /** 缺省 = default（事实节，CEO 可按 id 查）。off = 教练 / 机制选读，只给人看。 */
  ai?: ManualAiPlacement;
}

export interface ManualChapterContent {
  id: string;
  path: string;
  label: string;
  sections: ManualSection[];
}
