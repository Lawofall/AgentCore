import { SimpleTooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import {
  MANUAL_SECTION_IDS,
  manualHref,
} from "@/pages/toolbox/manual/sectionIds";
import { HelpCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";

/**
 * 产品手册深链（功能现场 ? 入口单一登记处）。
 * 节 ID 来自 sectionIds.ts，禁止手写 path 字符串。
 */
export const MANUAL_HELP = {
  debate: manualHref("collaboration", MANUAL_SECTION_IDS.collaboration.debate),
  checkpoint: manualHref(
    "collaboration",
    MANUAL_SECTION_IDS.collaboration.checkpoint,
  ),
  autonomy: manualHref(
    "collaboration",
    MANUAL_SECTION_IDS.collaboration.autonomy,
  ),
  control: manualHref(
    "collaboration",
    MANUAL_SECTION_IDS.collaboration.control,
  ),
} as const;

/**
 * 低调圆形「?」手册入口：hover 提示「看手册说明」，点击深链到产品手册对应节。
 * 功能现场（辩论室 / 审批 / 升级等）共用，形态统一。拍板卡与全屏协作画布不挂。
 */
export function ManualHelpLink({
  to,
  className,
}: {
  to: string;
  className?: string;
}) {
  const navigate = useNavigate();
  return (
    <SimpleTooltip label="看手册说明">
      <button
        type="button"
        aria-label="看手册说明"
        data-manual-help={to}
        className={cn(
          "inline-flex size-5 shrink-0 items-center justify-center rounded-full",
          "text-muted-foreground/60 transition-colors",
          "hover:bg-accent hover:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          className,
        )}
        onClick={(e) => {
          e.stopPropagation();
          navigate(to);
        }}
      >
        <HelpCircle size={12} strokeWidth={2} aria-hidden />
      </button>
    </SimpleTooltip>
  );
}

/**
 * 文字链手册入口（popover / 说明层内用）：文案统一「手册·XX」。
 */
export function ManualHelpTextLink({
  to,
  label,
  className,
}: {
  to: string;
  label: string;
  className?: string;
}) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      data-manual-help={to}
      className={cn(
        "text-xs text-primary hover:underline",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        className,
      )}
      onClick={(e) => {
        e.stopPropagation();
        navigate(to);
      }}
    >
      {label}
    </button>
  );
}
