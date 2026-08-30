import { MANUAL_HELP, ManualHelpTextLink } from "@/components/ManualHelpLink";
import { Button } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Info } from "lucide-react";
import { type DebateForm, debateFormBlurb } from "../model";

/** 全页唯一概念解释入口（正反交锋怎么读）。 */
export function HowToReadPopover({ form }: { form: DebateForm }) {
  const isDebate = form === "debate";
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          className="h-auto px-1.5 py-0.5 text-xs text-muted-foreground"
          icon={<Info size={13} />}
        >
          这场怎么读
        </Button>
      </PopoverTrigger>
      <PopoverContent className="max-w-sm text-sm" align="end">
        <p className="font-medium text-foreground">怎么读这场辩论</p>
        <p className="mt-2 text-muted-foreground">
          {isDebate ? debateFormBlurb(form) : "这场按原形态呈现发言与终审。"}
        </p>
        {isDebate && (
          <ul className="mt-3 space-y-2 text-xs text-muted-foreground">
            <li>
              <span className="font-medium text-foreground">质询</span>
              ：主持人发出必答追问，辩手逐条作答。
            </li>
          </ul>
        )}
        <p className="mt-3 border-t border-border pt-2">
          <ManualHelpTextLink to={MANUAL_HELP.debate} label="手册·辩论" />
        </p>
      </PopoverContent>
    </Popover>
  );
}
