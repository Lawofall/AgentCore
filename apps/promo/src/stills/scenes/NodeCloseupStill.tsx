import type { Node } from "@xyflow/react";
import { AbsoluteFill } from "remotion";
import { GraphStage } from "../../core/graph/GraphStage";

/*
 * 功能特写 still: ONE real AgentNode at native size, showing the full chip
 * vocabulary the product packs into a teammate card — 角色身份头像 + 运行态
 * presence dot + 模型档(强) + 深度 badge + 流式输出预览 + 用时·工具
 * 脚注. A feature-callout "anatomy" shot, so it deliberately puts the running
 * live-preview AND the completed-only 用时 chip on one card (a composite the
 * runtime never shows at a single instant) to label every signal in one image;
 * drop `durationMs` for a strictly-faithful running-only card.
 *
 * Rendered through GraphStage (one node, no edges) so it reuses the exact
 * ReactFlow context + real AgentNode the graph stills use — pixel-identical to
 * in-app. A <Still> renders frame 0, and styles.css neutralizes the card's CSS
 * animations (pulse) to their resting state, so no per-frame freeze is
 * needed; `_enterFrame:-100` / `_terminalFrame:null` keep the motion wrappers
 * settled all the same.
 */

const NODE_W = 210; // AgentNode is fixed w-[210px]
const NODE_H = 132; // running card: header + 2-line preview + 用时·工具 footer
const PAD = 72; // breathing room around the card (also the dot-grid backdrop)

// Unified 4:3 frame: height = card + padding, width extended to 4:3 (the card is
// narrower than 4:3, so it just gains a little extra side margin), card centered.
export const CLOSEUP_H = NODE_H + PAD * 2;
export const CLOSEUP_W = Math.max(NODE_W + PAD * 2, Math.ceil(CLOSEUP_H * (4 / 3)));

export function NodeCloseupStill() {
  const node: Node = {
    id: "closeup",
    type: "agent",
    position: { x: 0, y: 0 },
    data: {
      agentId: "closeup",
      runId: "closeup",
      // 3-char role so it shows in full on the fixed 210px header (a 5-char role
      // truncates to「数据…」— a feature-callout reads cleaner with the role intact).
      role: "分析师",
      status: "running",
      isAnimating: true,
      task: "汇总近 7 日成本趋势、定位异常点",
      outputPreview: "正在比对历史区间，已定位 2 处异常点，准备交叉验证…",
      tokenCount: 0,
      toolCount: 3,
      durationMs: 5200,
      focused: false,
      handleDirection: "horizontal",
      _enterFrame: -100,
      _terminalFrame: null,
      _ok: true,
    },
  };

  return (
    <AbsoluteFill className="bg-background">
      <GraphStage
        nodes={[node]}
        edges={[]}
        debate={null}
        frame={0}
        boxWidth={CLOSEUP_W}
        boxHeight={CLOSEUP_H}
        graphW={NODE_W}
        graphH={NODE_H}
        padX={PAD}
        padY={PAD}
        showBackground
      />
    </AbsoluteFill>
  );
}
