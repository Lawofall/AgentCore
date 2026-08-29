// @vitest-environment jsdom
/**
 * 发送时收口附件：只等附加阶段那一次上传（绝不重传）、多附件并行、失败带中文原因，
 * 暂存已失效的那几条要被点名摘掉；失败还要在 desktop.jsonl 留下可判层的一条。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/log", () => ({ logEvent: vi.fn() }));
vi.mock("../resideAttachment", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../resideAttachment")>();
  return { ...actual, ensureAttachmentResident: vi.fn() };
});

import { logEvent } from "@/lib/log";
import { ApiError, NetworkError } from "@/services/api";
import {
  __clearAttachmentUploadsForTests,
  rememberAttachmentRecover,
  trackAttachmentUpload,
} from "../attachmentUploads";
import type { PendingAttachment } from "../composerAttachments";
import {
  OVERSIZE_REASON,
  type ResideResult,
  ensureAttachmentResident,
} from "../resideAttachment";
import { settleAttachments } from "../settleAttachments";

const ensure = vi.mocked(ensureAttachmentResident);
const logged = vi.mocked(logEvent);

/** 本模块打出的失败日志（其余事件不看）。 */
function failureLogs() {
  return logged.mock.calls.filter(
    ([, event]) => event === "attachment.settle_failed",
  );
}

function onlyFailureLog(): {
  level: string;
  fields: Record<string, unknown>;
} {
  const calls = failureLogs();
  expect(calls).toHaveLength(1);
  return { level: calls[0][0], fields: calls[0][2] ?? {} };
}

function fileAttachment(
  over: Partial<PendingAttachment> = {},
): PendingAttachment {
  return {
    id: over.id ?? "a1",
    key: "dropped:a.png:1",
    name: "a.png",
    path: "a.png",
    text: "",
    truncated: false,
    kind: "file",
    binary: true,
    fileBlob: new File([new Uint8Array([1])], "a.png", { type: "image/png" }),
    ...over,
  };
}

function uploaded(name: string): ResideResult {
  return {
    ok: true,
    name,
    path: `attachments/${name}`,
    text: "",
    truncated: false,
    binary: true,
    workspacePath: `attachments/${name}`,
  };
}

beforeEach(() => {
  __clearAttachmentUploadsForTests();
  ensure.mockReset();
  logged.mockReset();
});

describe("settleAttachments", () => {
  it("复用附加时那次上传，不再重传一遍", async () => {
    const att = fileAttachment();
    trackAttachmentUpload(att.id, "c1", Promise.resolve(uploaded("a.png")));

    const res = await settleAttachments("c1", [att]);

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.outgoing).toEqual([
      {
        name: "a.png",
        path: "attachments/a.png",
        text: "",
        truncated: false,
        kind: "file",
        binary: true,
        workspace_path: "attachments/a.png",
      },
    ]);
    expect(ensure).not.toHaveBeenCalled();
  });

  it("仍在传就等它落地（不从头重来）", async () => {
    const att = fileAttachment();
    let release!: (r: ResideResult) => void;
    trackAttachmentUpload(
      att.id,
      "c1",
      new Promise<ResideResult>((r) => {
        release = r;
      }),
    );

    const pending = settleAttachments("c1", [att]);
    let settled = false;
    void pending.then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);

    release(uploaded("a.png"));
    const res = await pending;
    expect(res.ok).toBe(true);
    expect(ensure).not.toHaveBeenCalled();
  });

  it("多附件并行收口，不串行等待", async () => {
    const a = fileAttachment({ id: "a1" });
    const b = fileAttachment({
      id: "b1",
      name: "b.png",
      key: "dropped:b.png:1",
    });
    let started = 0;
    ensure.mockImplementation(async () => {
      started += 1;
      await new Promise((r) => setTimeout(r, 0));
      return {
        ok: true,
        workspacePath: "attachments/x.png",
        name: "x.png",
        binary: true,
        text: "",
        truncated: false,
      };
    });

    const pending = settleAttachments("c1", [a, b]);
    await Promise.resolve();
    expect(started).toBe(2);
    await pending;
  });

  it("附加时失败的附件在发送时重试一次", async () => {
    const att = fileAttachment();
    trackAttachmentUpload(
      att.id,
      "c1",
      Promise.resolve({ ok: false, reason: "上传附件到云端工作区失败" }),
    );
    ensure.mockResolvedValue({
      ok: true,
      workspacePath: "attachments/a.png",
      name: "a.png",
      binary: true,
      text: "",
      truncated: false,
    });

    const res = await settleAttachments("c1", [att]);

    expect(ensure).toHaveBeenCalledTimes(1);
    expect(res.ok).toBe(true);
  });

  it("目标会话对不上（草稿附件发进新建会话）就走兜底驻留", async () => {
    const att = fileAttachment();
    trackAttachmentUpload(att.id, null, Promise.resolve(uploaded("a.png")));
    ensure.mockResolvedValue({
      ok: true,
      workspacePath: "attachments/a.png",
      name: "a.png",
      binary: true,
      text: "",
      truncated: false,
    });

    await settleAttachments("new-conv", [att]);

    expect(ensure).toHaveBeenCalledWith(
      "new-conv",
      expect.objectContaining({
        name: att.name,
        fileBlob: att.fileBlob,
      }),
    );
  });

  it("暂存已失效：报中文原因并点名要摘掉的 chip", async () => {
    const att = fileAttachment({ stagingId: "stg-1", fileBlob: undefined });
    ensure.mockResolvedValue({
      ok: false,
      reason: "附件暂存已失效，请重新附加",
    });

    const res = await settleAttachments("c1", [att]);

    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("暂存已失效");
    expect(res.staleIds).toEqual([att.id]);
  });

  it("驻留失败时把原始错误一并交出去（toast 靠它说真实原因）", async () => {
    const att = fileAttachment();
    const refused = new Error("文件超出 52428800 字节的上传上限");
    ensure.mockResolvedValue({
      ok: false,
      reason: refused.message,
      cause: refused,
    });

    const res = await settleAttachments("c1", [att]);

    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.cause).toBe(refused);
    expect(res.reason).toBe("文件超出 52428800 字节的上传上限");
  });

  it("已删会话的 workspacePath 不能跳过：换会话要再驻留", async () => {
    const att = fileAttachment({
      workspacePath: "attachments/a.png",
      stagingId: "stg-1",
    });
    rememberAttachmentRecover(att.id, att.fileBlob, "old-conv");
    ensure.mockResolvedValue({
      ok: true,
      workspacePath: "attachments/a.png",
      name: "a.png",
      binary: true,
      text: "",
      truncated: false,
    });

    const res = await settleAttachments("new-conv", [att]);

    expect(res.ok).toBe(true);
    expect(ensure).toHaveBeenCalledWith(
      "new-conv",
      expect.objectContaining({
        workspacePath: undefined,
        fileBlob: att.fileBlob,
      }),
    );
  });

  it("已消耗 stagingId：用 recover blob 再驻留，不报暂存已失效", async () => {
    const blob = new File([new Uint8Array([1])], "a.png", {
      type: "image/png",
    });
    const att = fileAttachment({
      stagingId: "stg-consumed",
      fileBlob: undefined,
    });
    rememberAttachmentRecover(att.id, blob, "old-conv");
    ensure.mockResolvedValue({
      ok: true,
      workspacePath: "attachments/a.png",
      name: "a.png",
      binary: true,
      text: "",
      truncated: false,
    });

    const res = await settleAttachments("new-conv", [att]);

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.outgoing[0]).toMatchObject({
      workspace_path: "attachments/a.png",
    });
    expect(ensure).toHaveBeenCalledWith(
      "new-conv",
      expect.objectContaining({
        fileBlob: blob,
        stagingId: "stg-consumed",
      }),
    );
  });

  it("对话 / 目录这类纯文本引用原样透传，不碰驻留", async () => {
    const conv: PendingAttachment = {
      id: "c-1",
      key: "conversation:conversation:x",
      name: "上次讨论",
      path: "对话",
      text: "用户: hi",
      truncated: false,
      kind: "conversation",
      conversationId: "prev",
    };

    const res = await settleAttachments("c1", [conv]);

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.outgoing[0]).toMatchObject({
      kind: "conversation",
      conversation_id: "prev",
      text: "用户: hi",
    });
    expect(ensure).not.toHaveBeenCalled();
  });

  it("点名设定原样透传，不碰驻留", async () => {
    const pin: PendingAttachment = {
      id: "d-1",
      key: "document:setting:doc-1",
      name: "说话简短",
      path: "设定",
      text: "",
      truncated: false,
      kind: "document",
      documentId: "doc-1",
    };

    const res = await settleAttachments("c1", [pin]);

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.outgoing[0]).toMatchObject({
      kind: "document",
      document_id: "doc-1",
      path: "设定",
    });
    expect(ensure).not.toHaveBeenCalled();
  });
});

/**
 * 线上复现只能靠用户截图，是因为这条路以前一个字都不写。日志要能自己答出「哪一层」，
 * 又不能把文件名 / 路径 / 用户正文带进 desktop.jsonl。
 */
describe("settleAttachments 失败日志", () => {
  it("后端拒绝：一条 warn 记下 HTTP status 与错误 code", async () => {
    const denied = new ApiError(
      403,
      JSON.stringify({
        error: { code: "CSRF_TOKEN_INVALID", message: "请刷新后重试" },
      }),
    );
    ensure.mockResolvedValue({
      ok: false,
      reason: denied.message,
      cause: denied,
    });

    await settleAttachments("c1", [fileAttachment()]);

    const { level, fields } = onlyFailureLog();
    expect(level).toBe("warn");
    expect(fields).toMatchObject({
      via: "send",
      failure_kind: "http",
      status: 403,
      code: "CSRF_TOKEN_INVALID",
    });
  });

  it.each([
    {
      kind: "network",
      failure: {
        ok: false as const,
        reason: "network request failed",
        cause: new NetworkError(new TypeError("Failed to fetch")),
      },
    },
    {
      kind: "local_workspace_unavailable",
      failure: {
        ok: false as const,
        reason: "本地工作区目录不可用，请重新打开文件夹后再附加",
      },
    },
    {
      kind: "staging_expired",
      failure: { ok: false as const, reason: "附件暂存已失效，请重新附加" },
    },
    {
      kind: "oversize",
      failure: { ok: false as const, reason: OVERSIZE_REASON },
    },
  ])("没有 HTTP 应答的失败也判得出层：$kind", async ({ kind, failure }) => {
    ensure.mockResolvedValue(failure);

    await settleAttachments("c1", [fileAttachment()]);

    const { fields } = onlyFailureLog();
    expect(fields.failure_kind).toBe(kind);
    expect(fields.status).toBeUndefined();
    expect(fields.code).toBeUndefined();
  });

  it("三个附件一起失败也只记一条（记的是这次发送，不是每个附件）", async () => {
    ensure.mockResolvedValue({
      ok: false,
      reason: "本地工作区目录不可用，请重新打开文件夹后再附加",
    });

    await settleAttachments("c1", [
      fileAttachment({ id: "a1" }),
      fileAttachment({ id: "b1", name: "b.png", key: "dropped:b.png:1" }),
      fileAttachment({ id: "d1", name: "d.png", key: "dropped:d.png:1" }),
    ]);

    expect(failureLogs()).toHaveLength(1);
  });

  it("插话与普通发送靠 via 分得开", async () => {
    ensure.mockResolvedValue({
      ok: false,
      reason: "附件数据已失效，请重新附加",
    });

    await settleAttachments("c1", [fileAttachment()], "midflight");

    expect(onlyFailureLog().fields.via).toBe("midflight");
  });

  it("不写文件名 / 路径 / reason 原文——认不出的原因也只留 other", async () => {
    // 主进程的 fs 错误恰恰是最容易夹带绝对路径的那种 reason。
    const leaky =
      "EACCES: permission denied, open 'C:\\Users\\me\\季度复盘.xlsx'";
    ensure.mockResolvedValue({ ok: false, reason: leaky });

    await settleAttachments("c1", [
      fileAttachment({ name: "季度复盘.xlsx", path: "季度复盘.xlsx" }),
    ]);

    const { fields } = onlyFailureLog();
    expect(fields.failure_kind).toBe("other");
    const dumped = JSON.stringify(fields);
    expect(dumped).not.toContain("季度复盘");
    expect(dumped).not.toContain("C:\\Users");
    expect(dumped).not.toContain(leaky);
    expect(Object.keys(fields).sort()).toEqual([
      "code",
      "failure_kind",
      "status",
      "via",
    ]);
  });

  it("成功收口不打日志", async () => {
    const att = fileAttachment();
    trackAttachmentUpload(att.id, "c1", Promise.resolve(uploaded("a.png")));

    await settleAttachments("c1", [att]);

    expect(failureLogs()).toHaveLength(0);
  });
});
