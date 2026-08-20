import { turnVerdictFromProjected } from "@/lib/turnOutcome";
// `pnpm conformance` entry for the mobile fold (前端技术与架构 §十 SSE 与协议一致性).
// Runs the brand-new mobile fold against the backend-exported golden vectors and
// exits non-zero on any ProjectedTurn drift (CI gate). Desktop will add its own
// conformance script registering its fold-snapshot adapter against the same golden.
import { runConformance } from "@agentcore/protocol-conformance";
import { fold } from "./fold";
import { runParityChecks } from "./parity.check";

const { failed } = runConformance({
  name: "mobile",
  fold,
  verdict: turnVerdictFromProjected,
});
// 对等对账门禁 (parity gate): 桌面/协议新增「该上手机」的交互面、手机漏给对等裁决 → 这里红。
// 与 fold conformance 同挂一个门（pnpm conformance / CI mobile job）。锚 A（事件穷尽）另由
// typecheck 在编译期把关；此处跑锚 B（扫桌面 chat 目录）+ 裁决字段质量自检。
const parityProblems = runParityChecks();
process.exit(failed === 0 && parityProblems === 0 ? 0 : 1);
