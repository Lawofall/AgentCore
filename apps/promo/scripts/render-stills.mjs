/*
 * Batch-render the promo collaboration stills → out/stills/<id>.png at 2x DPI:
 *  - `appshell` — the 领衔 full-window shot (real desktop shell + the 对话级画布
 *    Canvas view running inside), registered standalone in Root.tsx (4:3, 1920×1440).
 *  - `payoff` — the 收束高潮 shot (demo butterfly delivered, CEO 汇聚点 lit), sized to
 *    the demo bbox framed 4:3.
 *  - `nodecard` — a 功能特写 of one real AgentNode (模型档 / 深度 / 流式预览 /
 *    用时·工具), also standalone in Root.tsx (sized to the card + margin).
 *  - `mobile` — 9:20 手机聊天页（fan-out SSE 向量 + 真机 fold/AssistantContent），供宣传图 #8
  - the 4 STILL_DEFS diagrams (fanout / debate / nested2 / bigteam), each already
 *    tightly cropped to its baked ELK bbox, so no manual cropping needed.
 *
 * Run:  pnpm stills        (cwd = apps/promo; needs `pnpm stills:layout` first if
 *                           STILL_DEFS changed)
 *
 * Each id maps to a `Still-<id>` composition registered via src/stills/manifest.ts.
 */
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";

// appshell + nodecard are standalone (Root.tsx); the rest mirror STILL_IDS in
// src/data/stills.ts.
const IDS = [
  "appshell",
  "payoff",
  "nodecard",
  "mobile",
  "fanout",
  "debate",
  "nested2",
  "bigteam",
  "bigteam-tall",
];

const SCALE = 2;
const OUT_DIR = "out/stills";

mkdirSync(OUT_DIR, { recursive: true });

for (const id of IDS) {
  const out = `${OUT_DIR}/${id}.png`;
  console.log(`\n▶ rendering ${id} → ${out}`);
  execSync(
    `npx remotion still Still-${id} ${out} --scale=${SCALE} --log=error`,
    { stdio: "inherit" },
  );
}

console.log(`\n✓ ${IDS.length} stills written to ${OUT_DIR}/ (scale ${SCALE}x)`);
