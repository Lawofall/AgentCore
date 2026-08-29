// @vitest-environment jsdom
/**
 * 回形针 / @ 本机文件这条路的「附加即上传」：主进程已经把字节暂存好了，云端会话下
 * 不该等用户点发送才开始「读回字节 → PUT」。chip 立刻出现并标上传中，发送时只等。
 */

import { act, renderHook } from "@testing-library/react";
import { useRef, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({ getConversations: () => [] }));
vi.mock("@/hooks/useFolders", () => ({ getFolders: vi.fn(() => []) }));
vi.mock("@/services/messages", () => ({ fetchMessageWindow: vi.fn() }));
vi.mock("@/services/documents", () => ({
  listScopeEntries: vi.fn(async () => []),
}));
vi.mock("@/services/workspaceBinding", () => ({
  getWorkspaceBinding: vi.fn(),
}));
vi.mock("@/lib/capabilities", () => ({ hasLocalFiles: () => true }));
vi.mock("@/lib/log", () => ({ logEvent: vi.fn() }));
vi.mock("../resideAttachment", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../resideAttachment")>();
  return {
    ...actual,
    pickLocalFileAttachment: vi.fn(),
    ensureAttachmentResident: vi.fn(),
  };
});

import { getFolders } from "@/hooks/useFolders";
import type { ComposerBodyHandle } from "../ComposerBodyEditor";
import { __clearAttachmentUploadsForTests } from "../attachmentUploads";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "../composerAttachments";
import {
  type ResidentAttachment,
  ensureAttachmentResident,
  pickLocalFileAttachment,
} from "../resideAttachment";
import { settleAttachments } from "../settleAttachments";
import { useMentionMenu } from "../useMentionMenu";

const pick = vi.mocked(pickLocalFileAttachment);
const ensure = vi.mocked(ensureAttachmentResident);

const CONV = "c1";

/** 主进程暂存成功（云端会话下没有 workspacePath，字节还压在 staging 里）。 */
function stagedPick() {
  return {
    ok: true as const,
    name: "report.xlsx",
    path: "report.xlsx",
    text: "",
    truncated: false,
    binary: true,
    stagingId: "stg-1",
  };
}

function uploaded(): ResidentAttachment {
  return {
    ok: true,
    workspacePath: "attachments/report.xlsx",
    name: "report.xlsx",
    binary: true,
    text: "",
    truncated: false,
  };
}

function deferred() {
  let resolve!: (r: ResidentAttachment) => void;
  const promise = new Promise<ResidentAttachment>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

/** 真 React state：chip 的函数式 patch 才观察得到。 */
function useMentionHarness(
  conversationId: string | null,
  onAttachmentFolderHint?: (hint: {
    folderId: string;
    folderName: string;
  }) => void,
) {
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [agentMentions, setAgentMentions] = useState<PendingAgentMention[]>([]);
  const caretRef = useRef(0);
  const bodyRef = useRef<ComposerBodyHandle | null>(null);
  if (bodyRef.current === null) {
    bodyRef.current = {
      focus: () => {},
      getCaret: () => caretRef.current,
      setCaret: (offset: number) => {
        caretRef.current = offset;
      },
    };
  }
  const mention = useMentionMenu({
    conversationId,
    value,
    setValue,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    bodyRef,
    onAttachmentFolderHint,
  });
  return { attachments, mention, value };
}

beforeEach(() => {
  __clearAttachmentUploadsForTests();
  pick.mockReset();
  ensure.mockReset();
  vi.mocked(getFolders).mockReturnValue([]);
});

describe("回形针附加即上传", () => {
  it("云端会话：chip 立刻标上传中，上传在附加时就开跑", async () => {
    pick.mockResolvedValue(stagedPick());
    const gate = deferred();
    ensure.mockReturnValue(gate.promise);
    const { result } = renderHook(() => useMentionHarness(CONV));

    await act(async () => {
      await result.current.mention.pickLocalFile();
    });

    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.value).toContain("\uFFFC");
    expect(result.current.attachments[0]).toMatchObject({
      name: "report.xlsx",
      uploadState: "uploading",
      stagingId: "stg-1",
    });
    expect(ensure).toHaveBeenCalledTimes(1);
    expect(ensure).toHaveBeenCalledWith(
      CONV,
      expect.objectContaining({ stagingId: "stg-1" }),
    );

    await act(async () => {
      gate.resolve(uploaded());
    });

    expect(result.current.attachments[0]).toMatchObject({
      path: "attachments/report.xlsx",
      workspacePath: "attachments/report.xlsx",
    });
    expect(result.current.attachments[0].uploadState).toBeUndefined();
    // 暂存字节已被取走：留着 id 只会让重启后的草稿发不出去。
    expect(result.current.attachments[0].stagingId).toBeUndefined();
  });

  it("发送收口复用附加时那次上传，不再传第二遍", async () => {
    pick.mockResolvedValue(stagedPick());
    ensure.mockResolvedValue(uploaded());
    const { result } = renderHook(() => useMentionHarness(CONV));

    await act(async () => {
      await result.current.mention.pickLocalFile();
    });
    expect(ensure).toHaveBeenCalledTimes(1);

    const settled = await settleAttachments(CONV, result.current.attachments);

    expect(settled.ok).toBe(true);
    if (!settled.ok) return;
    expect(settled.outgoing[0]).toMatchObject({
      workspace_path: "attachments/report.xlsx",
    });
    expect(ensure).toHaveBeenCalledTimes(1);
  });

  it("本机工作区已直写 attachments/：不再来一趟上传", async () => {
    pick.mockResolvedValue({
      ...stagedPick(),
      workspacePath: "attachments/report.xlsx",
    });
    const { result } = renderHook(() => useMentionHarness(CONV));

    await act(async () => {
      await result.current.mention.pickLocalFile();
    });

    expect(result.current.attachments[0].uploadState).toBeUndefined();
    expect(ensure).not.toHaveBeenCalled();
  });

  it("附加时上传失败：chip 留在草稿里并标出中文原因", async () => {
    pick.mockResolvedValue(stagedPick());
    ensure.mockResolvedValue({
      ok: false,
      reason: "上传附件到云端工作区失败",
    });
    const { result } = renderHook(() => useMentionHarness(CONV));

    await act(async () => {
      await result.current.mention.pickLocalFile();
    });

    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.attachments[0]).toMatchObject({
      uploadState: "error",
      uploadError: "上传附件到云端工作区失败",
      // 重试还得靠它。
      stagingId: "stg-1",
    });
  });

  it("草稿（尚无会话）：不发起上传，等建会话后由发送兜底", async () => {
    pick.mockResolvedValue(stagedPick());
    const { result } = renderHook(() => useMentionHarness(null));

    await act(async () => {
      await result.current.mention.pickLocalFile();
    });

    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.attachments[0].uploadState).toBeUndefined();
    expect(ensure).not.toHaveBeenCalled();
  });

  it("草稿回形针：已登记本机根内的文件 → 提示跟来源文件夹", async () => {
    vi.mocked(getFolders).mockReturnValue([
      {
        id: "f-docs",
        name: "文档",
        mode: "local",
        localRootId: "root-1",
        localSubpath: null,
      },
    ]);
    pick.mockResolvedValue({
      ...stagedPick(),
      citedRootId: "root-1",
      citedRelPath: "docs/report.xlsx",
    });
    const onHint = vi.fn();
    const { result } = renderHook(() => useMentionHarness(null, onHint));

    await act(async () => {
      await result.current.mention.pickLocalFile();
    });

    expect(result.current.attachments[0]).toMatchObject({
      citedRootId: "root-1",
      citedRelPath: "docs/report.xlsx",
    });
    expect(onHint).toHaveBeenCalledWith({
      folderId: "f-docs",
      folderName: "文档",
    });
  });
});
