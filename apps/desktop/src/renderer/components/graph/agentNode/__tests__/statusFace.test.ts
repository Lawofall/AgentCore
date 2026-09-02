import { describe, expect, it } from "vitest";
import { buildAgentNodePresentation } from "../presentation";
import {
  type AgentNodeData,
  buildRevisionBadge,
  failureDetailSentence,
  revisionFaceHint,
  revisionFeedbackSummary,
  revisionVersionBadge,
  statusFaceLabel,
} from "../shared";

describe("statusFaceLabel", () => {
  it("shows 排队中 for pending", () => {
    expect(statusFaceLabel("pending", null).text).toBe("排队中");
  });

  it("shows live elapsed for running workers", () => {
    const face = statusFaceLabel("running", null, 45);
    expect(face.text).toBe("执行中 · 45s");
    expect(face.tickElapsed).toBe(true);
  });

  it("formats live elapsed past a minute like completed duration", () => {
    expect(
      statusFaceLabel("running", null, 968, null, false, null, "thinking").text,
    ).toBe("思考中 · 16m 8s");
    expect(statusFaceLabel("completed", 968_000).text).toBe("已完成 · 16m 8s");
  });

  it("omits elapsed suffix before 1 second", () => {
    expect(statusFaceLabel("running", null, 0).text).toBe("执行中");
  });

  it("shows completion duration for finished runs", () => {
    expect(statusFaceLabel("completed", 45_000).text).toBe("已完成 · 45s");
    expect(statusFaceLabel("completed", null).text).toBe("已完成");
  });

  it("shows failure, cancelled, and skipped states", () => {
    // 无 error / failureKind 时 failureFaceLabel 默认「调用失败」（非笼统「失败」）
    expect(statusFaceLabel("failed", null).text).toBe("调用失败");
    expect(statusFaceLabel("cancelled", null).text).toBe("已停止");
    expect(statusFaceLabel("skipped", null).text).toBe("未执行");
  });

  it("surfaces productLanded failed face as 产出已落盘", () => {
    expect(
      statusFaceLabel(
        "failed",
        null,
        undefined,
        null,
        false,
        "上游模型服务暂时不可用（503），请稍后再试",
        null,
        null,
        "call",
        true,
      ).text,
    ).toBe("产出已落盘");
  });

  it("prefers failureKind over error text for the failed face", () => {
    expect(
      statusFaceLabel(
        "failed",
        null,
        undefined,
        null,
        false,
        "未通过契约：缺少必需的引用来源",
        null,
        null,
        "quality",
      ).text,
    ).toBe("未达标");
    expect(
      statusFaceLabel(
        "failed",
        null,
        undefined,
        null,
        false,
        "缺少必备章节：结论",
        null,
        null,
        "format",
      ).text,
    ).toBe("格式未过");
    expect(
      statusFaceLabel(
        "failed",
        null,
        undefined,
        null,
        false,
        "LLM 流在收尾时中断",
        null,
        null,
        "model",
      ).text,
    ).toBe("模型中断");
    expect(
      statusFaceLabel(
        "failed",
        null,
        undefined,
        null,
        false,
        "timeout",
        null,
        null,
        "call",
      ).text,
    ).toBe("调用失败");
  });

  it("shows phase-specific running labels", () => {
    expect(
      statusFaceLabel("running", null, 12, null, false, null, "thinking").text,
    ).toBe("思考中 · 12s");
    expect(
      statusFaceLabel(
        "running",
        null,
        undefined,
        null,
        false,
        null,
        "waiting_children",
      ).text,
    ).toBe("等待子团队");
    expect(
      statusFaceLabel("running", null, 3, null, false, null, "winding_down")
        .text,
    ).toBe("收尾中 · 3s");
    expect(
      statusFaceLabel(
        "running",
        null,
        undefined,
        null,
        false,
        null,
        "tool",
        "file_read",
      ).text,
    ).toBe("Read file");
  });

  it("keeps pending queued and skipped distinct from thinking", () => {
    expect(statusFaceLabel("pending", null).text).toBe("排队中");
    expect(statusFaceLabel("skipped", null).text).toBe("未执行");
  });

  it("shows 待命 / 未传唤 for witness seat runs", () => {
    expect(statusFaceLabel("pending", null, undefined, null, true).text).toBe(
      "待命",
    );
    expect(statusFaceLabel("skipped", null, undefined, null, true).text).toBe(
      "未传唤",
    );
    // Ordinary worker pending stays 排队中.
    expect(statusFaceLabel("pending", null, undefined, null, false).text).toBe(
      "排队中",
    );
  });
});

describe("revisionVersionBadge", () => {
  it("returns 续 ×N for continuation nodes only", () => {
    expect(revisionVersionBadge(0)).toBeNull();
    expect(revisionVersionBadge(1)).toBe("续 ×1");
    expect(revisionVersionBadge(2)).toBe("续 ×2");
    expect(revisionVersionBadge(3)).toBe("续 ×3");
  });
});

describe("revisionFeedbackSummary", () => {
  it("reads body from channel=continuation", () => {
    expect(
      revisionFeedbackSummary([
        { channel: "task", body: "起草" },
        {
          channel: "continuation",
          body: "  补一段风险对冲，并收紧结论口径。  ",
        },
      ]),
    ).toBe("补一段风险对冲，并收紧结论口径。");
  });

  it("returns null when continuation block missing or empty", () => {
    expect(
      revisionFeedbackSummary([{ channel: "task", body: "起草" }]),
    ).toBeNull();
    expect(
      revisionFeedbackSummary([{ channel: "continuation", body: "   " }]),
    ).toBeNull();
    expect(revisionFeedbackSummary(undefined)).toBeNull();
  });
});

describe("buildRevisionBadge", () => {
  it("hot-fix uses 续 ×N badge", () => {
    expect(
      buildRevisionBadge({
        isRevision: true,
        continuationIndex: 1,
        isDebate: false,
      }),
    ).toEqual({
      kind: "hotfix",
      label: "续 ×1",
      title: "同人接续 续 ×1",
    });
  });

  it("debate statement continuation uses 第 N 轮 (round preferred)", () => {
    expect(
      buildRevisionBadge({
        isRevision: true,
        continuationIndex: 1,
        round: 3,
        isDebate: true,
        beat: "statement",
      }),
    ).toEqual({
      kind: "debate",
      label: "第 3 轮",
      title: "第 3 轮",
    });
  });

  it("debate cross-exam is not a graph badge (folded into round node)", () => {
    expect(
      buildRevisionBadge({
        isRevision: true,
        continuationIndex: 1,
        round: 2,
        isDebate: true,
        beat: "cross_exam",
      }),
    ).toBeNull();
  });

  it("debate closing uses 结辩 (no round in label)", () => {
    expect(
      buildRevisionBadge({
        isRevision: true,
        continuationIndex: 3,
        round: 2,
        isDebate: true,
        beat: "closing",
      }),
    ).toEqual({
      kind: "debate",
      label: "结辩",
      title: "结辩",
    });
  });

  it("debate falls back to continuationIndex when round missing", () => {
    expect(
      buildRevisionBadge({
        isRevision: true,
        continuationIndex: 1,
        round: 0,
        isDebate: true,
      })?.label,
    ).toBe("第 2 轮");
  });

  it("skips non-continuation", () => {
    expect(
      buildRevisionBadge({
        isRevision: false,
        continuationIndex: 1,
        isDebate: false,
      }),
    ).toBeNull();
    expect(
      buildRevisionBadge({
        isRevision: true,
        continuationIndex: 0,
        isDebate: false,
      }),
    ).toBeNull();
  });
});

function baseNode(extra: Partial<AgentNodeData> = {}): AgentNodeData {
  return {
    agentId: "a1",
    role: "撰写员",
    runId: "r1",
    status: "completed",
    isAnimating: false,
    task: "起草",
    outputPreview: "",
    tokenCount: 0,
    toolCount: 0,
    focused: false,
    ...extra,
  };
}

describe("buildAgentNodePresentation revision face", () => {
  it("continuation exposes 按指示 hint and 续 ×N badge", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        isRevision: true,
        continuationIndex: 1,
        revisionSummary: "补一段风险对冲",
      }),
    );
    expect(p.revisionBadge).toEqual({
      kind: "hotfix",
      label: "续 ×1",
      title: "同人接续 续 ×1",
    });
    expect(p.revisionFaceHint).toBe("按指示：补一段风险对冲");
    expect(p.peekTags).toContain("接续 撰写员 的现场 · 续 ×1");
  });

  it("debate continuation badge is 第 N 轮 without 热修修订", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        isRevision: true,
        continuationIndex: 1,
        round: 2,
        stance: "pro",
        debateBeat: "statement",
        revisionSummary: "应被忽略的热修文案",
      }),
    );
    expect(p.revisionBadge).toEqual({
      kind: "debate",
      label: "第 2 轮",
      title: "第 2 轮",
    });
    expect(p.revisionFaceHint).toBeNull();
    expect(p.peekTags).toContain("第 2 轮");
    expect(p.peekTags.some((t) => t.includes("热修"))).toBe(false);
  });

  it("debate closing badge is 结辩; cross-exam has no graph badge", () => {
    const cx = buildAgentNodePresentation(
      baseNode({
        isRevision: true,
        continuationIndex: 1,
        round: 2,
        stance: "pro",
        debateBeat: "cross_exam",
      }),
    );
    expect(cx.revisionBadge).toBeNull();
    const closing = buildAgentNodePresentation(
      baseNode({
        isRevision: true,
        continuationIndex: 2,
        round: 2,
        stance: "con",
        debateBeat: "closing",
      }),
    );
    expect(closing.revisionBadge?.label).toBe("结辩");
  });

  it("debate round phase overrides running status face", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        status: "running",
        isAnimating: true,
        stance: "pro",
        debateRoundPhase: "质询作答中",
      }),
    );
    expect(p.statusFace.text).toBe("质询作答中");
    expect(p.statusFace.cls).toContain("primary");
  });

  it("settled 含质询 suffix and 质询作答失败 replace on status face", () => {
    const done = buildAgentNodePresentation(
      baseNode({
        status: "completed",
        durationMs: 88_000,
        stance: "pro",
        debateCrossExamMark: { label: "含质询", mode: "suffix" },
      }),
    );
    expect(done.statusFace.text).toBe("已完成 · 1m 28s · 含质询");

    const failed = buildAgentNodePresentation(
      baseNode({
        status: "failed",
        stance: "con",
        debateCrossExamMark: { label: "质询作答失败", mode: "replace" },
      }),
    );
    expect(failed.statusFace.text).toBe("质询作答失败");
    expect(failed.statusFace.cls).toContain("destructive");
  });

  it("surfaces llm abort as 模型中断 on face + curated sentence in peek", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        status: "failed",
        error: "模型响应中断，已保留已生成内容，可继续。",
        failureKind: "model",
        outputPreview: "",
      }),
    );
    expect(p.statusFace.text).toBe("模型中断");
    expect(p.statusFace.cls).toContain("destructive");
    expect(p.peekActivity).toEqual({
      heading: "失败原因",
      text: failureDetailSentence("model", null),
    });
  });

  it("never leaks raw run.error into the peek (infra / format-gate text)", () => {
    const gateError = "缺少必备章节：结论";
    const p = buildAgentNodePresentation(
      baseNode({
        status: "failed",
        error: gateError,
        failureKind: "format",
        outputPreview: "",
      }),
    );
    expect(p.peekActivity?.text).toBe(failureDetailSentence("format", null));
    expect(p.peekActivity?.text).not.toContain("缺少必备章节");
    expect(p.peekActivity?.text).not.toContain("结论");
  });

  it("peek keeps the saved-files fact when the run landed products", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        status: "failed",
        error: "ConnectError: upstream 503",
        failureKind: "call",
        productLanded: true,
        outputPreview: "",
      }),
    );
    expect(p.peekActivity?.text).toBe(failureDetailSentence("call", true));
    expect(p.peekActivity?.text).not.toContain("ConnectError");
  });

  it("surfaces quality failureKind as 未达标 even when error lacks keywords", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        status: "failed",
        error: "未通过契约：缺少必需的引用来源",
        failureKind: "quality",
        outputPreview: "",
      }),
    );
    expect(p.statusFace.text).toBe("未达标");
  });

  it("debate via participant group without stance", () => {
    // isDebateTaggedRun：stance 或白名单 group（debate:debate / red_team /
    // roundtable / witness）；禁 startsWith("debate:")——假 topic 不算辩手。
    const p = buildAgentNodePresentation(
      baseNode({
        isRevision: true,
        continuationIndex: 1,
        round: 2,
        group: "debate:debate",
        debateBeat: "statement",
      }),
    );
    expect(p.revisionBadge?.kind).toBe("debate");
    expect(p.revisionBadge?.label).toBe("第 2 轮");
  });

  it("run_phase waiting/winding suppresses residual thinking preview", () => {
    const waiting = buildAgentNodePresentation(
      baseNode({
        status: "running",
        isAnimating: true,
        phase: "waiting_children",
        reasoningPreview: "还在想上一拍…",
      }),
    );
    expect(waiting.statusFace.text).toBe("等待子团队");
    expect(waiting.liveThinking).toBe("");
    expect(waiting.peekActivity).toBeNull();

    const winding = buildAgentNodePresentation(
      baseNode({
        status: "running",
        isAnimating: true,
        phase: "winding_down",
        reasoningPreview: "不该再当思考中",
      }),
    );
    expect(winding.statusFace.text).toBe("收尾中");
    expect(winding.liveThinking).toBe("");
  });

  it("composing peek: write family is label + 字, no 正在生成", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        status: "running",
        isAnimating: true,
        toolProgress: { toolName: "file_write", chars: 2100 },
      }),
    );
    expect(p.peekActivity).toEqual({
      heading: "Write file",
      text: "2.1k 字",
    });
  });

  it("composing peek: non-write has no 字 and no verb heading", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        status: "running",
        isAnimating: true,
        toolProgress: { toolName: "web_search", chars: 1280 },
      }),
    );
    expect(p.peekActivity).toBeNull();
    expect(p.liveTool).toEqual({ toolName: "web_search", chars: 1280 });
  });
});

describe("buildAgentNodePresentation checkpoint face", () => {
  it("shows 待放行 as decision badge while pending", () => {
    const p = buildAgentNodePresentation(
      baseNode({ checkpoint: { status: "pending", decision: null } }),
    );
    expect(p.checkpointFace).toEqual(
      expect.objectContaining({ label: "待放行" }),
    );
    expect(p.visibleFaceBadges.has("checkpoint")).toBe(true);
  });

  it("shows 已放行 / 已调整 as process badges when resolved", () => {
    const released = buildAgentNodePresentation(
      baseNode({ checkpoint: { status: "resolved", decision: "continue" } }),
    );
    expect(released.checkpointFace?.label).toBe("已放行");
    expect(released.visibleFaceBadges.has("checkpoint")).toBe(true);

    const adjusted = buildAgentNodePresentation(
      baseNode({ checkpoint: { status: "resolved", decision: "adjust" } }),
    );
    expect(adjusted.checkpointFace?.label).toBe("已调整");
    expect(adjusted.visibleFaceBadges.has("checkpoint")).toBe(true);
  });

  it("keeps stop in the anomaly bucket (cancelled label)", () => {
    const p = buildAgentNodePresentation(
      baseNode({ checkpoint: { status: "resolved", decision: "stop" } }),
    );
    expect(p.checkpointFace?.label).toBe("已停止");
    expect(p.visibleFaceBadges.has("checkpoint")).toBe(true);
  });

  it("yields released checkpoint to higher-priority decision/anomaly badges", () => {
    const p = buildAgentNodePresentation(
      baseNode({
        checkpoint: { status: "resolved", decision: "continue" },
        escalationPending: 1,
        reviewConcern: "critical",
      }),
    );
    expect(p.checkpointFace?.label).toBe("已放行");
    expect(p.visibleFaceBadges.has("checkpoint")).toBe(false);
    expect(p.visibleFaceBadges.has("escalation")).toBe(true);
    expect(p.visibleFaceBadges.has("reviewConcern")).toBe(true);
  });
});

describe("revisionFaceHint", () => {
  it("prefixes 按指示", () => {
    expect(revisionFaceHint("收紧结论")).toBe("按指示：收紧结论");
    expect(revisionFaceHint(null)).toBeNull();
  });
});
