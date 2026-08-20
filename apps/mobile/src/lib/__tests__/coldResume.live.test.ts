import type { PausedTurnSummary } from "@/api/turn";
import { beforeEach, describe, expect, it } from "vitest";
import {
  clearColdInteractions,
  getColdInteraction,
  getColdInteractionSnapshot,
  markColdDeferred,
  markColdOrphaned,
  markColdResolved,
  markColdSubmitting,
  rekeyColdMessageId,
  reopenColdPending,
  upsertColdRequired,
} from "../coldInteractions";
import {
  resolveColdBindHostId,
  resolveColdResumeKeyFromHosts,
  selectVisibleColdResumes,
} from "../coldResume";

beforeEach(() => {
  clearColdInteractions();
});

const tpPayload = (checkpointId: string) => ({
  checkpoint_id: checkpointId,
  conversation_id: "conv-live",
  primitive: "delegate" as const,
  workers: [
    { run_id: "r1", role: "研究员", task: "调研", depends_on: [] as string[] },
  ],
  tools: ["file_write"],
  motion: "",
  form: "",
  sides: [] as string[],
  max_rounds: 0,
  thorough: true,
  headline: "预计 1 人开工",
});

describe("coldResume · live Interaction authority", () => {
  it("team_preview_required with stamp paints without recovery paused shell", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-server-tp",
      payload: tpPayload("tp-live"),
    });

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [
        {
          role: "assistant",
          id: "client-uuid",
          serverMessageId: "m-server-tp",
        },
      ],
    });

    expect(visible).toHaveLength(1);
    expect(visible[0]?.kind).toBe("team_preview");
    expect(visible[0]?.message_id).toBe("m-server-tp");
    expect(visible[0]?.checkpoint_id).toBe("tp-live");
    expect(visible[0]?.headline).toBe("预计 1 人开工");
  });

  it("entryToPausedSummary copies revision lineage from required payload", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-server-tp",
      payload: {
        ...tpPayload("tp-rev2"),
        revision: 2,
        revised_from: "tp-rev1",
        revision_note: "人太多，改成一个人做",
      },
    });
    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [
        {
          role: "assistant",
          id: "client-uuid",
          serverMessageId: "m-server-tp",
        },
      ],
    });
    expect(visible[0]?.revision).toBe(2);
    expect(visible[0]?.revised_from).toBe("tp-rev1");
    expect(visible[0]?.revision_note).toBe("人太多，改成一个人做");
  });

  it("does not paint clickable card before serverMessageId stamp", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "client-uuid",
      payload: tpPayload("tp-nostamp"),
    });

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [
        {
          role: "assistant",
          id: "client-uuid",
          serverMessageId: null,
        },
      ],
    });

    expect(visible).toHaveLength(0);
    expect(
      resolveColdResumeKeyFromHosts(
        [{ role: "assistant", id: "client-uuid", serverMessageId: null }],
        "client-uuid",
      ),
    ).toBeNull();
  });

  it("paints after stamp arrives (client-bound pending → rekey)", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "client-uuid",
      payload: tpPayload("tp-late-stamp"),
    });

    expect(
      selectVisibleColdResumes({
        conversationId: "conv-live",
        byId: getColdInteractionSnapshot(),
        paused: [],
        hosts: [
          { role: "assistant", id: "client-uuid", serverMessageId: null },
        ],
      }),
    ).toHaveLength(0);

    rekeyColdMessageId("client-uuid", "m-server-late");

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [
        {
          role: "assistant",
          id: "client-uuid",
          serverMessageId: "m-server-late",
        },
      ],
    });
    expect(visible).toHaveLength(1);
    expect(visible[0]?.message_id).toBe("m-server-late");
  });

  it("round 2+ new host required replaces tombstone and paints", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-turn1",
      payload: { checkpoint_id: "cp-reuse", question: "第一轮？" },
    });
    markColdResolved({
      kind: "ask_user",
      id: "cp-reuse",
      resolution: { decision: "continue" },
    });

    expect(
      selectVisibleColdResumes({
        conversationId: "conv-live",
        byId: getColdInteractionSnapshot(),
        paused: [],
        hosts: [{ role: "assistant", id: "t1", serverMessageId: "m-turn1" }],
      }),
    ).toHaveLength(0);

    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-turn2",
      payload: { checkpoint_id: "cp-reuse", question: "第二轮？" },
    });

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [
        { role: "assistant", id: "t1", serverMessageId: "m-turn1" },
        { role: "assistant", id: "t2", serverMessageId: "m-turn2" },
      ],
    });
    expect(visible).toHaveLength(1);
    expect(visible[0]?.message_id).toBe("m-turn2");
    expect(visible[0]?.question).toBe("第二轮？");
  });

  it("recovery paused shell fills when IX has no covering pending", () => {
    const shell = {
      message_id: "m-shell",
      checkpoint_id: "cp-shell",
      kind: "plan_review",
      user_message: "",
      user_message_id: "",
      question: "",
      form: "",
      headline: "",
      motion: "",
      primitive: "delegate",
      max_rounds: 0,
      thorough: true,
      browser_login: false,
      steps: [{ run_id: "r1", role: "研" }],
      pending: [],
    } as PausedTurnSummary;

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [shell],
      hosts: [{ role: "assistant", id: "m-shell", serverMessageId: "m-shell" }],
    });
    expect(visible).toHaveLength(1);
    expect(visible[0]?.checkpoint_id).toBe("cp-shell");
  });

  it("submitting + resume_deferred still paints with deferredBusyReason", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-deferred",
      payload: { checkpoint_id: "cp-deferred", question: "放行？" },
    });
    markColdSubmitting({
      kind: "ask_user",
      id: "cp-deferred",
      resolution: { decision: "continue" },
    });
    markColdDeferred({
      messageId: "m-deferred",
      conversationId: "conv-live",
      busyReason: "live_turn",
    });

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [
        {
          role: "assistant",
          id: "client",
          serverMessageId: "m-deferred",
        },
      ],
    });
    expect(visible).toHaveLength(1);
    expect(visible[0]?.interactionStatus).toBe("submitting");
    expect(visible[0]?.deferredBusyReason).toBe("live_turn");
    expect(visible[0]?.message_id).toBe("m-deferred");
  });

  // ChatPage.resume() 的 isPausedFrameGone 分支：挂起帧真的不在了（超保留期被清理 / 回合已
  // 重新生成或删除）。「已由另一端处理」不走这里——那种幂等成功是 200 + `resume_settled`。
  it("挂起帧真失效 → 卡作废（非 resolved）、不放回可点、恢复壳也不补画", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-gone",
      payload: { checkpoint_id: "cp-gone", question: "放行？" },
    });
    markColdSubmitting({
      kind: "ask_user",
      id: "cp-gone",
      resolution: { decision: "continue" },
    });

    markColdOrphaned("cp-gone", {
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-gone",
    });
    // 作废 ≠ 被答了：resolved 会让这张卡冒充「你这次点击生效了」。
    expect(getColdInteraction("cp-gone")?.status).toBe("orphaned");
    expect(getColdInteraction("cp-gone")?.status).not.toBe("resolved");

    // 放回可点只会请用户一点再点、次次 404 —— reopen 对作废卡必须无效。
    reopenColdPending("cp-gone");
    expect(getColdInteraction("cp-gone")?.status).toBe("orphaned");

    const shell = {
      message_id: "m-gone",
      checkpoint_id: "cp-gone",
      kind: "ask_user",
      user_message: "",
      user_message_id: "",
      question: "放行？",
      form: "",
      headline: "",
      motion: "",
      primitive: "delegate",
      max_rounds: 0,
      thorough: true,
      browser_login: false,
      steps: [],
      pending: [],
    } as PausedTurnSummary;
    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [shell],
      hosts: [{ role: "assistant", id: "m-gone", serverMessageId: "m-gone" }],
    });
    expect(visible).toEqual([]);
  });

  it("resolved IX suppresses recovery shell (follow settlement)", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-follow-ask",
      payload: { checkpoint_id: "cp-follow-ask", question: "怎么推进？" },
    });
    markColdResolved({ kind: "ask_user", id: "cp-follow-ask" });

    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-follow-tp",
      payload: tpPayload("cp-follow-tp"),
    });
    markColdResolved({ kind: "team_preview", id: "cp-follow-tp" });

    const askShell = {
      message_id: "m-follow-ask",
      checkpoint_id: "cp-follow-ask",
      kind: "ask_user",
      user_message: "",
      user_message_id: "",
      question: "怎么推进？",
      form: "",
      headline: "",
      motion: "",
      primitive: "delegate",
      max_rounds: 0,
      thorough: true,
      browser_login: false,
      steps: [],
      pending: [],
    } as PausedTurnSummary;
    const tpShell: PausedTurnSummary = {
      ...askShell,
      message_id: "m-follow-tp",
      checkpoint_id: "cp-follow-tp",
      kind: "team_preview",
      question: "",
      headline: "预计 1 人开工",
    };

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [askShell, tpShell],
      hosts: [
        {
          role: "assistant",
          id: "m-follow-ask",
          serverMessageId: "m-follow-ask",
        },
        {
          role: "assistant",
          id: "m-follow-tp",
          serverMessageId: "m-follow-tp",
        },
      ],
    });
    expect(visible).toEqual([]);
  });

  it("resolved adjust does not paint a ResumeCard", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-adj",
      payload: tpPayload("tp-adj"),
    });
    markColdResolved({
      kind: "team_preview",
      id: "tp-adj",
      resolution: { decision: "adjust", note: "改成两人" },
    });
    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [{ role: "assistant", id: "m-adj", serverMessageId: "m-adj" }],
    });
    expect(visible).toEqual([]);
  });

  it("resolved adjust yields to a newer pending team_preview", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-adj",
      payload: tpPayload("tp-old"),
    });
    markColdResolved({
      kind: "team_preview",
      id: "tp-old",
      resolution: { decision: "adjust" },
    });
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-adj",
      payload: tpPayload("tp-new"),
    });
    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: [{ role: "assistant", id: "m-adj", serverMessageId: "m-adj" }],
    });
    const kickoffs = visible.filter((v) => v.kind === "team_preview");
    expect(kickoffs).toHaveLength(1);
    expect(kickoffs[0]?.checkpoint_id).toBe("tp-new");
    expect(kickoffs[0]?.interactionStatus).toBe("pending");
  });

  it("two pending team_preview cards paint only the latest (IX + paused shell)", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-server-tp",
      payload: { checkpoint_id: "cp-ask-keep", question: "这次讨论怎么推进？" },
    });
    upsertColdRequired({
      kind: "plan_review",
      conversationId: "conv-live",
      messageId: "m-server-tp",
      payload: {
        checkpoint_id: "pr-keep",
        steps: [{ run_id: "r1", role: "研" }],
        pending: [],
      },
    });
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-server-tp",
      payload: { ...tpPayload("tp-old-ix"), headline: "旧 IX 开工卡" },
    });
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: "m-server-tp",
      payload: { ...tpPayload("tp-latest"), headline: "最新开工卡" },
    });

    const oldShell = {
      message_id: "m-server-tp",
      checkpoint_id: "tp-paused-old",
      kind: "team_preview",
      user_message: "",
      user_message_id: "",
      question: "",
      form: "",
      headline: "旧开工卡 · 预计 1 人",
      motion: "",
      primitive: "delegate",
      max_rounds: 0,
      thorough: true,
      browser_login: false,
      steps: [],
      pending: [],
    } as PausedTurnSummary;

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [oldShell],
      hosts: [
        {
          role: "assistant",
          id: "client-uuid",
          serverMessageId: "m-server-tp",
        },
      ],
    });
    expect(visible.map((v) => v.kind).sort()).toEqual([
      "ask_user",
      "plan_review",
      "team_preview",
    ]);
    const kickoffs = visible.filter((v) => v.kind === "team_preview");
    expect(kickoffs).toHaveLength(1);
    expect(kickoffs[0]?.checkpoint_id).toBe("tp-latest");
    expect(kickoffs[0]?.headline).toBe("最新开工卡");
  });
});

describe("coldResume · resolveColdBindHostId (投影键不断档)", () => {
  it("prefers resumeStamp over an unsealed preferred client bubble", () => {
    expect(
      resolveColdBindHostId(
        [{ role: "assistant", id: "client-new", serverMessageId: null }],
        "client-new",
        { resumeStamp: "m-same-turn" },
      ),
    ).toBe("m-same-turn");
  });

  it("backfills stamp from preferred host when sealed", () => {
    expect(
      resolveColdBindHostId(
        [
          {
            role: "assistant",
            id: "client-uuid",
            serverMessageId: "m-server",
          },
        ],
        "client-uuid",
      ),
    ).toBe("m-server");
  });

  it("does not nail unsealed preferred — returns empty when no stamp exists", () => {
    expect(
      resolveColdBindHostId(
        [{ role: "assistant", id: "client-only", serverMessageId: null }],
        "client-only",
      ),
    ).toBe("");
  });

  it("falls back to latest stamped host when preferred is unsealed", () => {
    expect(
      resolveColdBindHostId(
        [
          { role: "assistant", id: "old", serverMessageId: "m-old" },
          { role: "assistant", id: "new-unsealed", serverMessageId: null },
        ],
        "new-unsealed",
      ),
    ).toBe("m-old");
  });
});

describe("coldResume · same-turn ask_user continue → team_preview", () => {
  it("paints team_preview on the same stamped host after ask resolved", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-same",
      payload: { checkpoint_id: "cp-ask", question: "怎么推进？" },
    });
    markColdResolved({
      kind: "ask_user",
      id: "cp-ask",
      resolution: { decision: "continue" },
    });

    const hosts = [
      {
        role: "assistant" as const,
        id: "client-same",
        serverMessageId: "m-same",
      },
    ];

    // Resume path may briefly prefer the client bubble; bind must keep the stamp.
    const bindId = resolveColdBindHostId(hosts, "client-same", {
      resumeStamp: "m-same",
    });
    expect(bindId).toBe("m-same");

    upsertColdRequired({
      kind: "team_preview",
      conversationId: "conv-live",
      messageId: bindId,
      payload: tpPayload("tp-after-ask"),
    });

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts,
    });
    expect(visible).toHaveLength(1);
    expect(visible[0]?.kind).toBe("team_preview");
    expect(visible[0]?.message_id).toBe("m-same");
    expect(visible[0]?.checkpoint_id).toBe("tp-after-ask");
  });

  it("unsealed window: no clickable card until stamp; then paints", () => {
    const bindEmpty = resolveColdBindHostId(
      [{ role: "assistant", id: "client-gap", serverMessageId: null }],
      "client-gap",
    );
    expect(bindEmpty).toBe("");

    upsertColdRequired({
      kind: "plan_review",
      conversationId: "conv-live",
      messageId: bindEmpty,
      payload: {
        checkpoint_id: "pr-gap",
        steps: [{ run_id: "r1", role: "研" }],
        pending: [],
      },
    });

    expect(
      selectVisibleColdResumes({
        conversationId: "conv-live",
        byId: getColdInteractionSnapshot(),
        paused: [],
        hosts: [{ role: "assistant", id: "client-gap", serverMessageId: null }],
      }),
    ).toHaveLength(0);

    // Late stamp: empty messageId binds via resolve → latest stamped host.
    const hostsSealed = [
      {
        role: "assistant" as const,
        id: "client-gap",
        serverMessageId: "m-gap-late",
      },
    ];
    expect(resolveColdResumeKeyFromHosts(hostsSealed, "")).toBe("m-gap-late");

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts: hostsSealed,
    });
    expect(visible).toHaveLength(1);
    expect(visible[0]?.kind).toBe("plan_review");
    expect(visible[0]?.message_id).toBe("m-gap-late");
  });

  it("same-turn plan_review paints when bind uses resume stamp without second message_start", () => {
    upsertColdRequired({
      kind: "ask_user",
      conversationId: "conv-live",
      messageId: "m-resume-key",
      payload: { checkpoint_id: "cp1", question: "先确认？" },
    });
    markColdResolved({
      kind: "ask_user",
      id: "cp1",
      resolution: { decision: "continue" },
    });

    // New unsealed bubble appeared; resume stamp still authoritative.
    const hosts = [
      {
        role: "assistant" as const,
        id: "client-prior",
        serverMessageId: "m-resume-key",
      },
      {
        role: "assistant" as const,
        id: "client-unsealed",
        serverMessageId: null,
      },
    ];
    const bindId = resolveColdBindHostId(hosts, "client-unsealed", {
      resumeStamp: "m-resume-key",
    });
    expect(bindId).toBe("m-resume-key");

    upsertColdRequired({
      kind: "plan_review",
      conversationId: "conv-live",
      messageId: bindId,
      payload: {
        checkpoint_id: "pr-same",
        steps: [{ run_id: "r1", role: "研" }],
        pending: [],
      },
    });

    const visible = selectVisibleColdResumes({
      conversationId: "conv-live",
      byId: getColdInteractionSnapshot(),
      paused: [],
      hosts,
    });
    expect(visible).toHaveLength(1);
    expect(visible[0]?.kind).toBe("plan_review");
    expect(visible[0]?.message_id).toBe("m-resume-key");
  });
});
