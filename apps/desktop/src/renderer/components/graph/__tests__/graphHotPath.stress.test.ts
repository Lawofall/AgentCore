import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
/**
 * 协作图流式热路径压测（真实 store fold + Document 门控投影 + Live face×N）。
 * 模拟多 worker 并行 token 洪水：每 tick ≈ 一次 rAF flush。
 * 对齐生产：projectTurnGraph 仅在 document fingerprint 变时跑；每 tick 量 N×liveSig/derive。
 * 不进 CI 红线断言（环境噪声大）；跑完打印 / 写出 JSON，供掉帧归因。
 *
 * 跑：pnpm -C apps/desktop exec vitest run src/renderer/components/graph/__tests__/graphHotPath.stress.test.ts
 */
import { computeLayout, nodeSpacingForFitMode } from "@/lib/elk-layout";
import type { ElkGraphLayout } from "@/lib/graph-layout-utils";
import { computeLayoutHints } from "@/lib/layoutHints";
import {
  type ExecutionPlan,
  type RunFrame,
  execRuntime,
  useExecutionStore,
} from "@/stores/execution";
import { projectRuntime } from "@/stores/execution/hooks";
import { beforeEach, describe, expect, it } from "vitest";
import { INPUT_ID } from "../constants";
import { graphDocumentFingerprint } from "../graphDocument";
import { agentNodeLiveSig, deriveAgentNodeLive } from "../graphLive";
import { buildGraphStructure } from "../helpers";
import { projectTurnGraph } from "../projectTurnGraph";
import { buildGraphScene } from "../scene";

const WORKERS = 20;
const FLUSHES = 240; // ~4s @ 60Hz
const CHARS_PER_WORKER_PER_FLUSH = 120;
const MID = "stress-hotpath-mid";

function pct(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const i = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil((p / 100) * sorted.length) - 1),
  );
  return sorted[i] ?? 0;
}

function summarize(ms: number[]) {
  const sorted = [...ms].sort((a, b) => a - b);
  return {
    n: ms.length,
    sum: Math.round(ms.reduce((a, b) => a + b, 0) * 10) / 10,
    p50: Math.round(pct(sorted, 50) * 100) / 100,
    p95: Math.round(pct(sorted, 95) * 100) / 100,
    max: Math.round((sorted[sorted.length - 1] ?? 0) * 100) / 100,
    over8ms: ms.filter((v) => v > 8).length,
    over16ms: ms.filter((v) => v > 16).length,
  };
}

function makePlan(): ExecutionPlan {
  const agents = Array.from({ length: WORKERS }, (_, i) => ({
    id: `a${i + 1}`,
    role: `研究员${i + 1}`,
  }));
  const runs: ExecutionPlan["runs"] = [
    {
      id: "captain",
      agentId: "ceo",
      task: "",
      dependsOn: agents.map((_, i) => `r${i + 1}`),
      kind: "captain",
    },
    ...agents.map((a, i) => ({
      id: `r${i + 1}`,
      agentId: a.id,
      task: `并行调研 ${i + 1}`,
      dependsOn: [] as string[],
      kind: "agent" as const,
    })),
  ];
  return {
    id: "exec-stress-hotpath",
    planType: "multi_agent",
    taskSummary: "二十路并行长输出压测",
    agents: [{ id: "ceo", role: "CEO" }, ...agents],
    runs,
  };
}

function started(agentId: string, runId: string, t: number): RunFrame {
  return {
    t,
    kind: "run_started",
    agentId,
    runId,
    parentRunId: null,
    runKind: runId === "captain" ? "captain" : "agent",
    continuesRunId: null,
  };
}

function delta(
  agentId: string,
  runId: string,
  t: number,
  text: string,
): RunFrame {
  return { t, kind: "run_output_delta", runId, agentId, delta: text };
}

beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
});

describe("graph hot path stress (dense stream)", () => {
  it("measures flush + fold + scene + project + elk under 8-worker flood", async () => {
    const store = useExecutionStore.getState();
    const plan = makePlan();
    store.startExecution(plan, MID);

    const boot: RunFrame[] = [];
    let t = 1;
    for (let i = 0; i < WORKERS; i++) {
      boot.push(started(`a${i + 1}`, `r${i + 1}`, t++));
    }
    store.recordFrames(boot, MID);

    const chunk = "字".repeat(CHARS_PER_WORKER_PER_FLUSH);
    const flushMs: number[] = [];
    const foldMs: number[] = [];
    const liveSigMs: number[] = [];
    const liveFaceMs: number[] = [];
    const projectGatedMs: number[] = [];
    const tickMs: number[] = [];
    let projectRuns = 0;
    let lastDocFp = "";

    // First layout once (structure stable during flood).
    let positions: Record<string, { x: number; y: number }> = {};
    let edges: ReturnType<typeof buildGraphStructure>["rawEdges"] = [];
    let bbox: { width: number; height: number } | null = null;
    let groups: Awaited<ReturnType<typeof computeLayout>>["groups"] = [];
    {
      const rt0 = execRuntime(useExecutionStore.getState(), MID);
      const exec0 = projectRuntime(rt0);
      expect(exec0).toBeTruthy();
      if (!exec0) return;
      const { nodeIds, rawEdges, subTeams } = buildGraphStructure(
        exec0.runs,
        INPUT_ID,
        new Set(),
      );
      const elkT0 = performance.now();
      const layout = await computeLayout(
        nodeIds,
        rawEdges,
        "leftright" as ElkGraphLayout,
        { source: INPUT_ID, sink: "captain" },
        subTeams,
        nodeSpacingForFitMode("width"),
        undefined,
        computeLayoutHints(subTeams, rawEdges),
      );
      const elkMs = performance.now() - elkT0;
      positions = layout.positions;
      edges = rawEdges;
      bbox = { width: layout.width, height: layout.height };
      groups = layout.groups;

      // Warm one projection so later ticks match steady-state.
      const scene0 = buildGraphScene(exec0, {
        inputId: INPUT_ID,
        expandedUnits: new Set(),
      });
      projectTurnGraph({
        execution: exec0,
        scene: scene0,
        positions,
        nodeSizes: {},
        groups,
        bbox,
        actCards: [],
        edges,
        handleDirection: "horizontal",
        litRunId: null,
        litEndpointMessageId: null,
        captainRun: { id: "captain" },
        captainStatus: null,
        finalAnswer: null,
        captainSynthesisPreview: "",
        captainStatusCaption: null,
        taskMessage: null,
        activateNode: () => undefined,
        expandedUnits: new Set(),
        onToggleUnitExpand: undefined,
        injectOverlay: null,
        layoutKind: "leftright",
        onFocusAct: () => undefined,
        documentShell: true,
      });
      lastDocFp = graphDocumentFingerprint({
        execution: exec0,
        expandedUnits: new Set(),
        focusedActId: null,
        handleDirection: "horizontal",
      });

      // Attach elk to report via closure below.
      (globalThis as { __stressElkMs?: number }).__stressElkMs = elkMs;
    }

    for (let f = 0; f < FLUSHES; f++) {
      const batch: RunFrame[] = [];
      for (let i = 0; i < WORKERS; i++) {
        batch.push(delta(`a${i + 1}`, `r${i + 1}`, t++, chunk));
      }
      const tick0 = performance.now();
      const tFlush = performance.now();
      store.recordFrames(batch, MID);
      flushMs.push(performance.now() - tFlush);

      const rt = execRuntime(useExecutionStore.getState(), MID);
      const tFold = performance.now();
      const exec = projectRuntime(rt);
      foldMs.push(performance.now() - tFold);
      if (!exec) throw new Error("missing exec");

      const docFp = graphDocumentFingerprint({
        execution: exec,
        expandedUnits: new Set(),
        focusedActId: null,
        handleDirection: "horizontal",
      });
      const tProj = performance.now();
      if (docFp !== lastDocFp) {
        lastDocFp = docFp;
        projectRuns += 1;
        const scene = buildGraphScene(exec, {
          inputId: INPUT_ID,
          expandedUnits: new Set(),
        });
        const projected = projectTurnGraph({
          execution: exec,
          scene,
          positions,
          nodeSizes: {},
          groups,
          bbox,
          actCards: [],
          edges,
          handleDirection: "horizontal",
          litRunId: null,
          litEndpointMessageId: null,
          captainRun: { id: "captain" },
          captainStatus: null,
          finalAnswer: null,
          captainSynthesisPreview: "",
          captainStatusCaption: null,
          taskMessage: null,
          activateNode: () => undefined,
          expandedUnits: new Set(),
          onToggleUnitExpand: undefined,
          injectOverlay: null,
          layoutKind: "leftright",
          onFocusAct: () => undefined,
          documentShell: true,
        });
        expect(projected.nodes.length).toBeGreaterThan(WORKERS);
      }
      projectGatedMs.push(performance.now() - tProj);

      // Production Live path: N× cheap sig + N× derive (only dirty nodes derive in React;
      // here we force all running workers to measure worst-case face CPU).
      const tSig = performance.now();
      let sigTouch = 0;
      for (let i = 0; i < WORKERS; i++) {
        sigTouch += agentNodeLiveSig(exec, `r${i + 1}`).length;
      }
      liveSigMs.push(performance.now() - tSig);

      const tFace = performance.now();
      let faceTouch = 0;
      for (let i = 0; i < WORKERS; i++) {
        const run = exec.runs.find((r) => r.id === `r${i + 1}`);
        if (!run) continue;
        const live = deriveAgentNodeLive(exec, run, {
          scene: null,
          litRunId: null,
          enterIndex: i,
          unitExpanded: false,
        });
        faceTouch += live.outputPreview.length + live.tokenCount;
      }
      liveFaceMs.push(performance.now() - tFace);
      expect(sigTouch + faceTouch).toBeGreaterThan(0);

      tickMs.push(performance.now() - tick0);
    }

    const rtEnd = execRuntime(useExecutionStore.getState(), MID);
    const liveFace = summarize(liveFaceMs);
    const liveSig = summarize(liveSigMs);
    const projectGated = summarize(projectGatedMs);
    const report = {
      scenario: "20-worker dense stream (document-gated + liveFace×N)",
      workers: WORKERS,
      flushes: FLUSHES,
      charsPerWorkerPerFlush: CHARS_PER_WORKER_PER_FLUSH,
      totalCharsApprox: FLUSHES * WORKERS * CHARS_PER_WORKER_PER_FLUSH,
      framesAtEnd: rtEnd.frames.length,
      projectRuns,
      elkOnceMs:
        Math.round(
          ((globalThis as { __stressElkMs?: number }).__stressElkMs ?? 0) * 100,
        ) / 100,
      flush: summarize(flushMs),
      fold: summarize(foldMs),
      liveSig,
      liveFace,
      projectGated,
      tick: summarize(tickMs),
      // Rough: if tick p95 > 16ms, main-thread alone can't hold 60fps even before paint.
      verdict: {
        tickP95Over16ms: summarize(tickMs).p95 > 16,
        documentGateHolds: projectRuns <= 2,
        liveFaceDominates:
          liveFace.p95 >= summarize(flushMs).p95 &&
          liveFace.p95 >= projectGated.p95,
      },
    };

    // eslint-disable-next-line no-console
    console.log("[graph-hotpath-stress]", JSON.stringify(report, null, 2));

    const outDir = resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../../../../shoot-out-graph-perf",
    );
    mkdirSync(outDir, { recursive: true });
    const outFile = resolve(outDir, "hotpath-cpu.json");
    writeFileSync(outFile, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    // eslint-disable-next-line no-console
    console.log(`[graph-hotpath-stress] wrote ${outFile}`);

    // Sanity: work happened; not a CI budget gate.
    expect(report.framesAtEnd).toBeGreaterThan(FLUSHES);
    expect(report.tick.n).toBe(FLUSHES);
    expect(report.verdict.documentGateHolds).toBe(true);
  }, 120_000);
});
