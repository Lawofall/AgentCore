import { EMBED_MIN_HEIGHT } from "@agentcore/graph-layout";
import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { ScenarioGraph } from "./EmbeddedGraphCanvas";
import { SCENARIOS } from "./scenarioData";
import { LazyMount } from "./shared";

/**
 * 机制场景：真实协作图画廊，单列、按需懒挂载。四个常用形态（并行 / 串行 / 正反辩论 /
 * 再开一层）常驻；进阶形态（执行中 / 再往下拆一层 / 接着做 / 多段同时开）
 * 折进「更多形态」，点开再挂。
 */
export function MechanismScenarios() {
  const [showMore, setShowMore] = useState(false);
  const canonical = SCENARIOS.filter((s) => !s.advanced);
  const more = SCENARIOS.filter((s) => s.advanced);
  return (
    <div className="space-y-8">
      {canonical.map((s) => (
        <LazyMount key={s.title} minHeight={EMBED_MIN_HEIGHT + 56}>
          <ScenarioGraph scenario={s} />
        </LazyMount>
      ))}
      {!showMore ? (
        <button
          type="button"
          onClick={() => setShowMore(true)}
          className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-border bg-card py-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <ChevronDown size={16} />
          展开更多形态（{more.length}）
        </button>
      ) : (
        more.map((s) => (
          <LazyMount key={s.title} minHeight={EMBED_MIN_HEIGHT + 56}>
            <ScenarioGraph scenario={s} />
          </LazyMount>
        ))
      )}
    </div>
  );
}
