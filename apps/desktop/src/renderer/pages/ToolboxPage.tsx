import { PageContainer } from "@/components/layout/PageContainer";
import {
  Badge,
  Button,
  Card,
  CatalogIconShell,
  SectionLabel,
} from "@/components/ui";
import { type ArtifactKind, artifactColorVar } from "@/lib/catalogColors";
import { cn } from "@/lib/utils";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import {
  BookOpen,
  ChevronRight,
  type LucideIcon,
  Palette,
  Plug,
  ScrollText,
  Timer,
  Workflow,
  Wrench,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

interface ToolboxEntry {
  id: string;
  title: string;
  description: string;
  icon: LucideIcon;
  color: ArtifactKind;
  /** 可用项点击跳转的子路由；占位（即将上线）项不设。 */
  to?: string;
}

/** 了解平台：产品手册入口，与能力项同视觉语言，靠首组位置区分。 */
const MANUAL: ToolboxEntry = {
  id: "manual",
  title: "产品手册",
  description: "从上手到玩转，到看懂团队怎么运转",
  icon: BookOpen,
  color: "manual",
  to: APP_PATHS.toolbox.manual.root,
};

// 只列真能点进去的创作工具。文档 / 思维导图 / 多维表格 / 幻灯片 / 可运行产物属未开工方向，
// 不以「即将开放」占位卡形式占首屏 → docs/01-产品/产品路线图摘要.md「交付物形态扩展」。
const CREATION_TOOLS: ToolboxEntry[] = [
  {
    id: "canvas",
    title: "白板",
    description: "自由排布画布可用；AI 指挥白板即将上线",
    icon: Palette,
    color: "canvas",
    to: "/whiteboard",
  },
];

// 「能力」组：AI 自身的能力（工具 + AI 提示词）+ 自动化 + 平台集成（连接器 / MCP）。
// 能力图鉴只分两类——工具（确定性代码）与 AI 提示词（含准则与按需注入的工具进阶用法 / 薄技能）。
const CAPABILITIES: ToolboxEntry[] = [
  {
    id: "tools",
    title: "工具",
    description: "Agent 可调用的动作工具，含可用性与调用参数",
    icon: Wrench,
    color: "tools",
    to: APP_PATHS.toolbox.tools,
  },
  {
    id: "guidelines",
    title: "AI 提示词",
    description:
      "全员准则、三选一角色身份、队员交付合同，以及按需注入的工具进阶用法",
    icon: ScrollText,
    color: "guidelines",
    to: APP_PATHS.toolbox.guidelines,
  },
  {
    id: "automations",
    title: "自动化",
    description: "定时或 Webhook 触发后，自动开一轮协作",
    icon: Timer,
    color: "workflow",
    to: APP_PATHS.toolbox.automations.root,
  },
  {
    id: "workflows",
    title: "工作流",
    description: "可保存的团队拆法：画布定义步骤与等人关卡",
    icon: Workflow,
    color: "workflow",
    to: APP_PATHS.toolbox.workflows.root,
  },
  {
    id: "connectors",
    title: "连接器",
    description: "MCP 与第三方服务接入",
    icon: Plug,
    color: "connectors",
    to: APP_PATHS.toolbox.connectors,
  },
];

const TOOLBOX_TILE_GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4";

function ToolboxSectionHeader({
  label,
  meta,
  className,
}: {
  label: string;
  meta?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "col-span-full flex items-baseline justify-between gap-3",
        className,
      )}
    >
      <SectionLabel>{label}</SectionLabel>
      {meta ? (
        <span className="shrink-0 text-xs text-muted-foreground/60">
          {meta}
        </span>
      ) : null}
    </div>
  );
}

function ToolboxTileCard({
  entry,
  comingSoon = false,
}: {
  entry: ToolboxEntry;
  comingSoon?: boolean;
}) {
  const navigate = useNavigate();
  const { icon: Icon, title, description, to, color } = entry;
  const available = Boolean(to);
  const colorVar = artifactColorVar(color);

  const inner = (
    <Card
      className={cn(
        "flex h-full w-full min-w-0 flex-col gap-3 p-4",
        available && "shadow-sm transition-shadow group-hover:shadow-md",
      )}
      variant={available ? "interactive" : "default"}
    >
      <div className="flex items-start justify-between gap-2">
        <CatalogIconShell colorVar={colorVar} muted={comingSoon}>
          <Icon size={18} />
        </CatalogIconShell>
        {comingSoon ? (
          <Badge tone="muted" pill className="shrink-0">
            即将开放
          </Badge>
        ) : available ? (
          <ChevronRight
            size={14}
            className="mt-0.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
          />
        ) : null}
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
        <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
          {description}
        </p>
      </div>
    </Card>
  );

  if (!to) {
    return inner;
  }

  return (
    <Button
      variant="ghost"
      onClick={() => navigate(to)}
      className="group !flex h-full w-full min-w-0 flex-col items-stretch justify-start p-0 text-left font-normal"
    >
      {inner}
    </Button>
  );
}

export function ToolboxPage() {
  return (
    <PageContainer width="canvas">
      <header className="pb-2">
        <h1 className="text-xl font-semibold text-foreground">工具箱</h1>
        <p className="mt-2 max-w-xl text-sm text-muted-foreground">
          AI 工具与创作工具，人与 Agent 协同的能力中心
        </p>
      </header>

      <div className={cn("mt-8", TOOLBOX_TILE_GRID)}>
        <ToolboxSectionHeader label="了解平台" />
        <ToolboxTileCard entry={MANUAL} />

        <ToolboxSectionHeader
          className="mt-6"
          label="能力"
          meta={`${CAPABILITIES.filter((e) => e.to).length} 项可用`}
        />
        {CAPABILITIES.map((entry) => (
          <ToolboxTileCard
            key={entry.id}
            entry={entry}
            comingSoon={!entry.to}
          />
        ))}

        <ToolboxSectionHeader
          className="mt-6"
          label="创作工具"
          meta={`${CREATION_TOOLS.length} 项`}
        />
        {CREATION_TOOLS.map((entry) => (
          <ToolboxTileCard
            key={entry.id}
            entry={entry}
            comingSoon={!entry.to}
          />
        ))}
      </div>
    </PageContainer>
  );
}
