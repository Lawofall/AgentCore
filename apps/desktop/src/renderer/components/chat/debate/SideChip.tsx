import { Badge } from "@/components/ui/badge";
import type React from "react";
import { ModelBadge } from "./ModelBadge";
import { shouldShowModelBadge } from "./model";

/**
 * 立场 / 视角名身份徽章：中性灰底 + 身份色圆点（内联 var，遵 color-tokens 身份色板——不与状态色
 * 竞争、不做大面积色块）。简报的各方速览、叙事发言格、记分卡共用同一只 pill → live↔收场、结论↔
 * 过程，同一方恒同色同形，可顺色追踪一方的论点链。
 */
export function SideNamePill({
  name,
  colorVar,
  showDot = true,
}: {
  name: string;
  colorVar: string;
  /** 质询等已有上下文标识方时，可关色点减噪。 */
  showDot?: boolean;
}) {
  return (
    <Badge tone="muted" pill className="gap-1.5 font-medium">
      {showDot && (
        <span
          className="size-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: colorVar }}
          aria-hidden
        />
      )}
      {name}
    </Badge>
  );
}

/** 辩手字母头像：中性底 + 身份色内描边环（替代整块染色头像）。 */
export function sideAvatarStyle(colorVar: string): React.CSSProperties {
  return {
    boxShadow: `inset 0 0 0 2px color-mix(in oklch, ${colorVar} 35%, transparent)`,
  };
}

/**
 * 一方的统一身份标识 = 身份名 pill + 模型徽章（按需）。模型徽章仅在身份名**不**已含厂商名时才显
 * （{@link shouldShowModelBadge}），消除「原生DeepSeek · DeepSeek」这类重复——名是语义立场时
 * 才把「由哪个模型驱动」作为第二维补出来。简报速览与发言格共用，标识在全页恒定一致。
 */
export function SideIdentity({
  name,
  colorVar,
  model,
}: {
  name: string;
  colorVar: string;
  model?: string | null;
}) {
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1.5">
      <SideNamePill name={name} colorVar={colorVar} />
      {shouldShowModelBadge(name, model) && <ModelBadge model={model} />}
    </span>
  );
}
