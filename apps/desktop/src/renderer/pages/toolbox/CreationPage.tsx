import { Badge, CATALOG_GRID_CLASS, CatalogTile } from "@/components/ui";
import { type ArtifactKind, artifactColorVar } from "@/lib/catalogColors";
import {
  FileText,
  type LucideIcon,
  PenLine,
  Presentation,
  Share2,
  Table2,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

const UNAVAILABLE = "尚未开放";

type CreationTool = {
  kind: ArtifactKind;
  title: string;
  description: string;
  icon: LucideIcon;
  href?: string;
};

const CREATION_TOOLS: CreationTool[] = [
  {
    kind: "canvas",
    title: "白板",
    description: "无限画布，自由摆元素。",
    icon: PenLine,
    href: "/whiteboard",
  },
  {
    kind: "doc",
    title: "文档",
    description: "可反复编辑的长文。",
    icon: FileText,
  },
  {
    kind: "mindmap",
    title: "思维导图",
    description: "可展开收起的结构图。",
    icon: Share2,
  },
  {
    kind: "table",
    title: "多维表格",
    description: "带类型列的表。",
    icon: Table2,
  },
  {
    kind: "slides",
    title: "幻灯片",
    description: "按页翻的文稿。",
    icon: Presentation,
  },
];

/** 工具箱 · 创作。白板可点进列表；未开工四项 muted，不可点。 */
export function CreationPage() {
  const navigate = useNavigate();
  return (
    <div className={CATALOG_GRID_CLASS}>
      {CREATION_TOOLS.map((tool) => {
        const Icon = tool.icon;
        const href = tool.href;
        return (
          <CatalogTile
            key={tool.kind}
            icon={<Icon size={18} />}
            colorVar={artifactColorVar(tool.kind)}
            title={tool.title}
            description={tool.description}
            muted={!href}
            accessory={
              href ? undefined : (
                <Badge tone="muted" pill>
                  {UNAVAILABLE}
                </Badge>
              )
            }
            onClick={href ? () => navigate(href) : undefined}
          />
        );
      })}
    </div>
  );
}
