// @vitest-environment jsdom
/**
 * 引用即驻留：ensureAttachmentResident 本地 finalize / 云端 PUT 分支（纯 mock，不碰磁盘）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/workspaceBinding", () => ({
  getWorkspaceBinding: vi.fn(),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveConversationLocalTarget: vi.fn(),
}));
vi.mock("@/services/workspace", () => ({
  uploadWorkspaceFile: vi.fn(),
}));
vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => false),
}));

import { hasLocalFiles } from "@/lib/capabilities";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";
import { uploadWorkspaceFile } from "@/services/workspace";
import { getWorkspaceBinding } from "@/services/workspaceBinding";
import {
  ATTACH_MAX_BYTES,
  ensureAttachmentResident,
  prepareBrowserFileAttachment,
  residentAttachmentForFile,
  safeBrowserFileName,
} from "../resideAttachment";

const getBinding = vi.mocked(getWorkspaceBinding);
const resolveTarget = vi.mocked(resolveConversationLocalTarget);
const upload = vi.mocked(uploadWorkspaceFile);
const hasLocal = vi.mocked(hasLocalFiles);

describe("ensureAttachmentResident", () => {
  beforeEach(() => {
    getBinding.mockReset();
    resolveTarget.mockReset();
    upload.mockReset();
    // jsdom: restore a clean fsApi each test
    (window as unknown as { fsApi?: unknown }).fsApi = undefined;
  });

  it("skips when workspacePath already set", async () => {
    const res = await ensureAttachmentResident("c1", {
      name: "a.xlsx",
      workspacePath: "attachments/a.xlsx",
      binary: true,
      text: "",
      truncated: false,
    });
    expect(res).toEqual({
      ok: true,
      workspacePath: "attachments/a.xlsx",
      name: "a.xlsx",
      binary: true,
      text: "",
      truncated: false,
    });
    expect(resolveTarget).not.toHaveBeenCalled();
    expect(upload).not.toHaveBeenCalled();
  });

  it("legacy text-only attachment (no stagingId) returns empty workspacePath", async () => {
    const res = await ensureAttachmentResident("c1", {
      name: "讨论",
      text: "用户: hi",
      truncated: false,
    });
    expect(res).toEqual({
      ok: true,
      workspacePath: "",
      name: "讨论",
      binary: false,
      text: "用户: hi",
      truncated: false,
    });
  });

  it("binary without bytes fails instead of legacy empty path", async () => {
    const res = await ensureAttachmentResident("c1", {
      name: "a.bin",
      binary: true,
      text: "",
      truncated: false,
    });
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("失效");
  });

  it("local branch finalizes staged attachment into workspace", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "conversation",
      rootId: "root-1",
      source: "explicit",
    });
    resolveTarget.mockResolvedValue({ rootId: "root-1", subpath: "scratch" });
    const finalize = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        name: "notes.md",
        workspacePath: "attachments/notes.md",
        binary: false,
        text: "# hi",
        truncated: false,
        sizeBytes: 4,
      },
    });
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      finalizeStagedAttachment: finalize,
    };

    const res = await ensureAttachmentResident("c1", {
      name: "notes.md",
      stagingId: "stg-1",
      text: "# hi",
      truncated: false,
    });

    expect(res).toEqual({
      ok: true,
      workspacePath: "attachments/notes.md",
      name: "notes.md",
      binary: false,
      text: "# hi",
      truncated: false,
    });
    expect(finalize).toHaveBeenCalledWith("stg-1", {
      rootId: "root-1",
      subpath: "scratch",
    });
    expect(upload).not.toHaveBeenCalled();
  });

  it("cites an in-tree file instead of finalize into attachments/", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "conversation",
      rootId: "root-1",
      source: "explicit",
    });
    resolveTarget.mockResolvedValue({ rootId: "root-1", subpath: "" });
    const finalize = vi.fn();
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      finalizeStagedAttachment: finalize,
    };

    const res = await ensureAttachmentResident("c1", {
      name: "guide.md",
      stagingId: "stg-cite",
      citedRootId: "root-1",
      citedRelPath: "docs/guide.md",
      text: "# hi",
      truncated: false,
    });

    expect(res).toEqual({
      ok: true,
      workspacePath: "docs/guide.md",
      name: "guide.md",
      binary: false,
      text: "# hi",
      truncated: false,
    });
    expect(finalize).not.toHaveBeenCalled();
    expect(upload).not.toHaveBeenCalled();
  });

  it("strips dest subpath when citing", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "conversation",
      rootId: "root-1",
      source: "explicit",
    });
    resolveTarget.mockResolvedValue({ rootId: "root-1", subpath: "work/pkg" });
    const finalize = vi.fn();
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      finalizeStagedAttachment: finalize,
    };

    const res = await ensureAttachmentResident("c1", {
      name: "guide.md",
      stagingId: "stg-cite",
      citedRootId: "root-1",
      citedRelPath: "work/pkg/docs/guide.md",
      text: "# hi",
      truncated: false,
    });

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workspacePath).toBe("docs/guide.md");
    expect(finalize).not.toHaveBeenCalled();
  });

  it("finalizes when the cited file sits outside dest", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "conversation",
      rootId: "root-1",
      source: "explicit",
    });
    resolveTarget.mockResolvedValue({ rootId: "root-1", subpath: "work/pkg" });
    const finalize = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        name: "guide.md",
        workspacePath: "attachments/guide.md",
        binary: false,
        text: "# hi",
        truncated: false,
        sizeBytes: 4,
      },
    });
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      finalizeStagedAttachment: finalize,
    };

    const res = await ensureAttachmentResident("c1", {
      name: "guide.md",
      stagingId: "stg-out",
      citedRootId: "root-1",
      citedRelPath: "other/guide.md",
      text: "# hi",
      truncated: false,
    });

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workspacePath).toBe("attachments/guide.md");
    expect(finalize).toHaveBeenCalledWith("stg-out", {
      rootId: "root-1",
      subpath: "work/pkg",
    });
  });

  it("local mode without usable root refuses cloud PUT", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "conversation",
      rootId: "root-1",
      source: "explicit",
    });
    // dest null → resolveAttachDest returns null (no finalize path)
    resolveTarget.mockResolvedValue(null);
    const consume = vi.fn();
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      consumeStagedBytes: consume,
    };

    const res = await ensureAttachmentResident("c1", {
      name: "x.bin",
      stagingId: "stg-2",
      text: "",
      truncated: false,
      binary: true,
    });

    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("本地工作区");
    expect(consume).not.toHaveBeenCalled();
    expect(upload).not.toHaveBeenCalled();
  });

  it("cloud branch consumes staged bytes and PUTs attachments/", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    resolveTarget.mockResolvedValue(null);
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
    const consume = vi.fn().mockResolvedValue({
      ok: true,
      data: { name: "report.xlsx", data: bytes, binary: true },
    });
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      consumeStagedBytes: consume,
    };
    upload.mockResolvedValue(undefined as never);

    const res = await ensureAttachmentResident("c1", {
      name: "report.xlsx",
      stagingId: "stg-3",
      text: "",
      truncated: false,
      binary: true,
    });

    expect(res).toEqual({
      ok: true,
      workspacePath: "attachments/report.xlsx",
      name: "report.xlsx",
      binary: true,
      text: "",
      truncated: false,
      fileBlob: expect.any(File),
    });
    expect(consume).toHaveBeenCalledWith("stg-3");
    expect(upload).toHaveBeenCalledWith(
      "c1",
      "attachments/report.xlsx",
      expect.any(Blob),
    );
  });

  it("cloud upload failure surfaces reason", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    resolveTarget.mockResolvedValue(null);
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      consumeStagedBytes: vi.fn().mockResolvedValue({
        ok: true,
        data: {
          name: "a.bin",
          data: new Uint8Array([1]),
          binary: true,
        },
      }),
    };
    const denied = new Error("409 conflict");
    upload.mockRejectedValue(denied);

    const res = await ensureAttachmentResident("c1", {
      name: "a.bin",
      stagingId: "stg-4",
      text: "",
      truncated: false,
      binary: true,
    });
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("409");
    // 原始错误留在 cause 上：后端 ApiError 的 code / serverMessage 只有它带得动。
    expect(res.cause).toBe(denied);
  });

  it("web fileBlob draft PUTs to attachments/ on ensure", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    upload.mockResolvedValue(undefined as never);
    const blob = new File([new Uint8Array([0x50, 0x4b])], "pack.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });

    const res = await ensureAttachmentResident("c1", {
      name: "pack.docx",
      text: "",
      truncated: false,
      binary: true,
      fileBlob: blob,
    });

    expect(res).toEqual({
      ok: true,
      workspacePath: "attachments/pack.docx",
      name: "pack.docx",
      binary: true,
      text: "",
      truncated: false,
      fileBlob: blob,
    });
    expect(upload).toHaveBeenCalledWith("c1", "attachments/pack.docx", blob);
  });
});

describe("prepareBrowserFileAttachment", () => {
  beforeEach(() => {
    getBinding.mockReset();
    upload.mockReset();
  });

  it("allows images as binary (draft holds fileBlob)", async () => {
    const file = new File(["x"], "pic.png", { type: "image/png" });
    const res = await prepareBrowserFileAttachment(null, file);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.binary).toBe(true);
    expect(res.text).toBe("");
    expect(res.fileBlob).toBe(file);
    expect(res.name).toBe("pic.png");
    expect(upload).not.toHaveBeenCalled();
  });

  it("rejects oversize files", async () => {
    const file = new File(["x"], "big.bin");
    Object.defineProperty(file, "size", { value: ATTACH_MAX_BYTES + 1 });
    const res = await prepareBrowserFileAttachment(null, file);
    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("50MB");
  });

  it("draft without conversationId holds fileBlob (binary allowed)", async () => {
    const bytes = new Uint8Array([0x00, 0x01, 0x02]);
    const file = new File([bytes], "data.bin", {
      type: "application/octet-stream",
    });
    const res = await prepareBrowserFileAttachment(null, file);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.binary).toBe(true);
    expect(res.fileBlob).toBe(file);
    expect(res.workspacePath).toBeUndefined();
    expect(upload).not.toHaveBeenCalled();
  });

  it("with conversationId immediately PUTs text file", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    upload.mockResolvedValue(undefined as never);
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    const res = await prepareBrowserFileAttachment("c9", file);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workspacePath).toBe("attachments/notes.txt");
    expect(res.fileBlob).toBeUndefined();
    expect(res.text).toBe("hello");
    expect(upload).toHaveBeenCalledWith("c9", "attachments/notes.txt", file);
  });

  it("云端 PUT 失败：原始错误随结果带出，不只剩一句 message", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    const refused = new Error("文件超出 52428800 字节的上传上限");
    upload.mockRejectedValue(refused);

    const res = await prepareBrowserFileAttachment(
      "c9",
      new File(["hello"], "notes.txt", { type: "text/plain" }),
    );

    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toBe("文件超出 52428800 字节的上传上限");
    expect(res.cause).toBe(refused);
  });

  it("safeBrowserFileName strips path and leading dots", () => {
    expect(safeBrowserFileName("..\\foo/bar.txt")).toBe("bar.txt");
    expect(safeBrowserFileName("...")).toBe("attachment");
  });
});

describe("residentAttachmentForFile（附加即上传）", () => {
  beforeEach(() => {
    getBinding.mockReset();
    resolveTarget.mockReset();
    upload.mockReset();
    hasLocal.mockReturnValue(true);
    (window as unknown as { fsApi?: unknown }).fsApi = undefined;
  });

  it("云端会话：渲染进程直接 PUT File，不经主进程暂存", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    upload.mockResolvedValue(undefined as never);
    const stageDroppedFile = vi.fn();
    const consumeStagedBytes = vi.fn();
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      stageDroppedFile,
      consumeStagedBytes,
    };
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });

    const res = await residentAttachmentForFile("c1", file);

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workspacePath).toBe("attachments/notes.txt");
    expect(res.text).toBe("hello");
    // 关键：PUT 的就是原 File，没有「写盘 → 读回 → 重新包 Blob」那几次拷贝。
    expect(upload).toHaveBeenCalledWith("c1", "attachments/notes.txt", file);
    expect(stageDroppedFile).not.toHaveBeenCalled();
    expect(consumeStagedBytes).not.toHaveBeenCalled();
  });

  it("本机工作区：交主进程复制，渲染进程不 PUT", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "folder",
      rootId: "root-1",
      source: "explicit",
    });
    resolveTarget.mockResolvedValue({ rootId: "root-1", subpath: "" });
    const stageDroppedFile = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        name: "notes.txt",
        workspacePath: "attachments/notes.txt",
        binary: false,
        text: "hello",
        truncated: false,
      },
    });
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      stageDroppedFile,
    };

    const res = await residentAttachmentForFile(
      "c1",
      new File(["hello"], "notes.txt", { type: "text/plain" }),
    );

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workspacePath).toBe("attachments/notes.txt");
    expect(stageDroppedFile).toHaveBeenCalled();
    expect(upload).not.toHaveBeenCalled();
  });

  it("桌面草稿：暂存留底的同时把 File 留在内存（发送时直传，不再读回字节）", async () => {
    const stageDroppedFile = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        name: "shot.png",
        binary: true,
        text: "",
        truncated: false,
        stagingId: "stg-9",
      },
    });
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      stageDroppedFile,
    };
    const file = new File([new Uint8Array([0x89])], "shot.png", {
      type: "image/png",
    });

    const res = await residentAttachmentForFile(null, file);

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.stagingId).toBe("stg-9");
    expect(res.fileBlob).toBe(file);
    expect(getBinding).not.toHaveBeenCalled();
    expect(upload).not.toHaveBeenCalled();
  });

  it("桌面草稿暂存失败时退回内存 File，不把附件丢掉", async () => {
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      stageDroppedFile: vi
        .fn()
        .mockResolvedValue({ ok: false, reason: "暂存附件失败" }),
    };
    const file = new File(["hi"], "a.txt", { type: "text/plain" });

    const res = await residentAttachmentForFile(null, file);

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.fileBlob).toBe(file);
    expect(res.stagingId).toBeUndefined();
  });

  it("超过上限直接拒，不发起任何驻留", async () => {
    const big = new File(["x"], "big.bin");
    Object.defineProperty(big, "size", { value: ATTACH_MAX_BYTES + 1 });
    const stageDroppedFile = vi.fn();
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      stageDroppedFile,
    };

    const res = await residentAttachmentForFile("c1", big);

    expect(res.ok).toBe(false);
    if (res.ok) return;
    expect(res.reason).toContain("50MB");
    expect(stageDroppedFile).not.toHaveBeenCalled();
    expect(upload).not.toHaveBeenCalled();
  });
});

describe("ensureAttachmentResident 兼顾暂存与内存 File", () => {
  beforeEach(() => {
    getBinding.mockReset();
    resolveTarget.mockReset();
    upload.mockReset();
    (window as unknown as { fsApi?: unknown }).fsApi = undefined;
  });

  it("云端：同时有暂存 id 和内存 File 时走 File 直传，不读回字节", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    resolveTarget.mockResolvedValue(null);
    const consume = vi.fn();
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      finalizeStagedAttachment: vi.fn(),
      consumeStagedBytes: consume,
    };
    upload.mockResolvedValue(undefined as never);
    const blob = new File([new Uint8Array([1, 2])], "shot.png", {
      type: "image/png",
    });

    const res = await ensureAttachmentResident("c1", {
      name: "shot.png",
      stagingId: "stg-1",
      binary: true,
      text: "",
      truncated: false,
      fileBlob: blob,
    });

    expect(res).toEqual({
      ok: true,
      workspacePath: "attachments/shot.png",
      name: "shot.png",
      binary: true,
      text: "",
      truncated: false,
      fileBlob: blob,
    });
    expect(consume).not.toHaveBeenCalled();
    expect(upload).toHaveBeenCalledWith("c1", "attachments/shot.png", blob);
  });

  it("本机工作区：即便手里有 File 也先 finalize 暂存，别误传云端", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "folder",
      rootId: "root-1",
      source: "explicit",
    });
    resolveTarget.mockResolvedValue({ rootId: "root-1", subpath: "" });
    const finalize = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        name: "shot.png",
        workspacePath: "attachments/shot.png",
        binary: true,
        text: "",
        truncated: false,
      },
    });
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      finalizeStagedAttachment: finalize,
    };

    const res = await ensureAttachmentResident("c1", {
      name: "shot.png",
      stagingId: "stg-1",
      binary: true,
      text: "",
      truncated: false,
      fileBlob: new File([new Uint8Array([1])], "shot.png"),
    });

    expect(res.ok).toBe(true);
    expect(finalize).toHaveBeenCalledWith("stg-1", {
      rootId: "root-1",
      subpath: undefined,
    });
    expect(upload).not.toHaveBeenCalled();
  });

  it("旧 workspacePath + fileBlob：仍 PUT 进当前会话，不拿已删路径跳过", async () => {
    getBinding.mockResolvedValue({
      mode: "cloud",
      scope: "conversation",
      rootId: null,
      source: "container",
    });
    resolveTarget.mockResolvedValue(null);
    upload.mockResolvedValue(undefined as never);
    const blob = new File([new Uint8Array([1, 2])], "shot.png", {
      type: "image/png",
    });

    const res = await ensureAttachmentResident("new-conv", {
      name: "shot.png",
      workspacePath: "attachments/shot.png",
      binary: true,
      text: "",
      truncated: false,
      fileBlob: blob,
    });

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workspacePath).toBe("attachments/shot.png");
    expect(upload).toHaveBeenCalledWith(
      "new-conv",
      "attachments/shot.png",
      blob,
    );
  });

  it("已消耗 stagingId + fileBlob：本机 finalize 失效则再暂存进当前工作区", async () => {
    getBinding.mockResolvedValue({
      mode: "local",
      scope: "folder",
      rootId: "root-1",
      source: "explicit",
    });
    resolveTarget.mockResolvedValue({ rootId: "root-1", subpath: "" });
    const finalize = vi.fn().mockResolvedValue({
      ok: false,
      reason: "附件暂存已失效，请重新附加",
    });
    const stageDroppedFile = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        name: "shot.png",
        workspacePath: "attachments/shot.png",
        binary: true,
        text: "",
        truncated: false,
      },
    });
    (window as unknown as { fsApi: Record<string, unknown> }).fsApi = {
      finalizeStagedAttachment: finalize,
      stageDroppedFile,
    };
    const blob = new File([new Uint8Array([1])], "shot.png", {
      type: "image/png",
    });

    const res = await ensureAttachmentResident("new-conv", {
      name: "shot.png",
      stagingId: "stg-consumed",
      binary: true,
      text: "",
      truncated: false,
      fileBlob: blob,
    });

    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workspacePath).toBe("attachments/shot.png");
    expect(finalize).toHaveBeenCalledWith("stg-consumed", {
      rootId: "root-1",
      subpath: undefined,
    });
    expect(stageDroppedFile).toHaveBeenCalledWith(blob, {
      rootId: "root-1",
      subpath: undefined,
    });
    expect(upload).not.toHaveBeenCalled();
  });
});
