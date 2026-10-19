import { describe, expect, it } from "vitest";
import {
  IpcInvalidArgsError,
  assertShape,
  ipcInvalidArgsLogFields,
  isRecord,
  requireStringFields,
} from "../ipc-validate";

describe("ipc-validate（IPC 边界结构校验 · IPC-004）", () => {
  describe("isRecord", () => {
    it("接受对象、拒绝原始值与 null", () => {
      expect(isRecord({})).toBe(true);
      expect(isRecord({ a: 1 })).toBe(true);
      expect(isRecord(null)).toBe(false);
      expect(isRecord(undefined)).toBe(false);
      expect(isRecord("x")).toBe(false);
      expect(isRecord(42)).toBe(false);
    });
  });

  describe("requireStringFields", () => {
    it("全部键为 string 时返回窄化对象", () => {
      expect(
        requireStringFields({ rootId: "r", relPath: "a/b" }, [
          "rootId",
          "relPath",
        ]),
      ).toEqual({ rootId: "r", relPath: "a/b" });
    });

    it("任一键缺失 / 非 string / 非对象时返回 null", () => {
      expect(
        requireStringFields({ rootId: "r" }, ["rootId", "relPath"]),
      ).toBeNull();
      expect(
        requireStringFields({ rootId: 1, relPath: "x" }, ["rootId", "relPath"]),
      ).toBeNull();
      expect(requireStringFields(null, ["rootId"])).toBeNull();
      expect(requireStringFields("nope", ["rootId"])).toBeNull();
      // 数组虽是对象，但不含命名键 → 失败（防止以数组冒充 payload）。
      expect(requireStringFields([], ["rootId"])).toBeNull();
    });

    it("只取列出的键、忽略多余字段", () => {
      expect(
        requireStringFields({ rootId: "r", extra: 9 }, ["rootId"]),
      ).toEqual({
        rootId: "r",
      });
    });
  });

  describe("assertShape", () => {
    it("形状合法时静默通过", () => {
      expect(() =>
        assertShape("c", { rootId: "r", turnId: "t" }, ["rootId", "turnId"]),
      ).not.toThrow();
    });

    it("可选 string 缺省放行、存在且为 string 放行", () => {
      expect(() =>
        assertShape("c", { rootId: "r" }, ["rootId"], ["subpath"]),
      ).not.toThrow();
      expect(() =>
        assertShape(
          "c",
          { rootId: "r", subpath: "sub" },
          ["rootId"],
          ["subpath"],
        ),
      ).not.toThrow();
    });

    it("可选 string 存在但非 string 时抛 IpcInvalidArgsError 并点名字段", () => {
      expect(() =>
        assertShape("c", { rootId: "r", subpath: 5 }, ["rootId"], ["subpath"]),
      ).toThrow(IpcInvalidArgsError);
      try {
        assertShape("c", { rootId: "r", subpath: 5 }, ["rootId"], ["subpath"]);
      } catch (err) {
        expect(err).toBeInstanceOf(IpcInvalidArgsError);
        const e = err as IpcInvalidArgsError;
        expect(e.field).toBe("subpath");
        expect(e.expected).toBe("string");
        expect(e.message).toMatch(/subpath/);
      }
    });

    it("必需键缺失 / 非对象时抛 IpcInvalidArgsError", () => {
      expect(() =>
        assertShape("c", { rootId: "r" }, ["rootId", "turnId"]),
      ).toThrow(IpcInvalidArgsError);
      expect(() => assertShape("c", null, ["rootId"])).toThrow(
        IpcInvalidArgsError,
      );
    });

    it("错误信息含通道名与字段，便于排查", () => {
      expect(() => assertShape("sidecar:startTurn", {}, ["rootId"])).toThrow(
        /sidecar:startTurn.*rootId/,
      );
    });

    it("可空标识缺省 / null / string 放行，数字与对象拒绝", () => {
      expect(() =>
        assertShape("c", { rootId: "r" }, ["rootId"], [], ["runId"]),
      ).not.toThrow();
      expect(() =>
        assertShape(
          "c",
          { rootId: "r", runId: null },
          ["rootId"],
          [],
          ["runId"],
        ),
      ).not.toThrow();
      expect(() =>
        assertShape(
          "c",
          { rootId: "r", runId: "member-1" },
          ["rootId"],
          [],
          ["runId"],
        ),
      ).not.toThrow();
      expect(() =>
        assertShape("c", { rootId: "r", runId: 42 }, ["rootId"], [], ["runId"]),
      ).toThrow(IpcInvalidArgsError);
      expect(() =>
        assertShape(
          "c",
          { rootId: "r", runId: { id: "x" } },
          ["rootId"],
          [],
          ["runId"],
        ),
      ).toThrow(IpcInvalidArgsError);
      try {
        assertShape("c", { rootId: "r", runId: 42 }, ["rootId"], [], ["runId"]);
      } catch (err) {
        expect(err).toBeInstanceOf(IpcInvalidArgsError);
        const e = err as IpcInvalidArgsError;
        expect(e.field).toBe("runId");
        expect(e.expected).toBe("string | null");
      }
    });

    it("reorderQueuedTurns：queueIds 是字符串数组，不是单个字符串", () => {
      const required = ["rootId", "conversationId"] as const;
      expect(() =>
        assertShape(
          "sidecar:reorderQueuedTurns",
          {
            rootId: "r",
            conversationId: "c",
            queueIds: ["q1", "q2"],
          },
          required,
          ["subpath"],
          [],
          ["queueIds"],
        ),
      ).not.toThrow();
      expect(() =>
        assertShape(
          "sidecar:reorderQueuedTurns",
          { rootId: "r", conversationId: "c", queueIds: "q1" },
          required,
          ["subpath"],
          [],
          ["queueIds"],
        ),
      ).toThrow(IpcInvalidArgsError);
    });

    it("runStop：runId null（停整队）放行，脏值仍拒", () => {
      const required = ["rootId", "conversationId", "executionId"] as const;
      expect(() =>
        assertShape(
          "sidecar:runStop",
          {
            rootId: "r",
            conversationId: "c",
            executionId: "e",
            runId: null,
          },
          required,
          ["subpath"],
          ["runId"],
        ),
      ).not.toThrow();
      expect(() =>
        assertShape(
          "sidecar:runStop",
          {
            rootId: "r",
            conversationId: "c",
            executionId: "e",
            runId: true,
          },
          required,
          ["subpath"],
          ["runId"],
        ),
      ).toThrow(IpcInvalidArgsError);
    });

    it("未列入 optionalStrings 的对象载荷（session）不拦合法 startTurn", () => {
      // 回归：对象载荷勿塞进 optionalStrings，否则合法请求会被拒。
      // session 是会话快照对象，寻址 string 校验 + 对象透传才是正确姿态。
      const startTurnRequired = [
        "rootId",
        "conversationId",
        "turnId",
        "traceId",
        "userMessage",
        "userMessageId",
        "messageId",
      ] as const;
      const startTurnOptionalStrings = ["subpath"] as const;
      expect(() =>
        assertShape(
          "sidecar:startTurn",
          {
            rootId: "r",
            conversationId: "c",
            turnId: "t",
            traceId: "a".repeat(32),
            userMessage: "hi",
            userMessageId: "u",
            messageId: "m",
            subpath: "scratch",
            session: {
              permissionAxes: { boundary: "folder" },
              delegateMode: true,
              folderId: null,
              localRootId: null,
              localSubpath: null,
            },
          },
          startTurnRequired,
          startTurnOptionalStrings,
        ),
      ).not.toThrow();
    });

    it("resume 同构：session 对象 + 可选 string 并存时放行", () => {
      const resumeRequired = [
        "rootId",
        "conversationId",
        "messageId",
        "traceId",
        "decision",
        "note",
      ] as const;
      const resumeOptionalStrings = ["subpath", "userMessageId"] as const;
      expect(() =>
        assertShape(
          "sidecar:resume",
          {
            rootId: "r",
            conversationId: "c",
            messageId: "m",
            traceId: "b".repeat(32),
            decision: "continue",
            note: "",
            userMessageId: "u",
            session: {
              permissionAxes: { boundary: "computer" },
              delegateMode: false,
              folderId: null,
              localRootId: null,
              localSubpath: null,
            },
          },
          resumeRequired,
          resumeOptionalStrings,
        ),
      ).not.toThrow();
    });
  });

  describe("ipcInvalidArgsLogFields（sidecar.ipc_invalid_args 载荷）", () => {
    it("抽出 channel / field / expected 与寻址 id，不落正文", () => {
      const err = new IpcInvalidArgsError(
        "sidecar:startTurn",
        "permissionAxes",
        "string",
      );
      expect(
        ipcInvalidArgsLogFields(err, {
          rootId: "root-1",
          conversationId: "conv-1",
          userMessage: "secret body must not appear as a dedicated field",
          permissionAxes: { boundary: "folder" },
        }),
      ).toEqual({
        channel: "sidecar:startTurn",
        field: "permissionAxes",
        expected: "string",
        conversation_id: "conv-1",
        root_id: "root-1",
      });
    });

    it("payload 非对象时仍给出 channel 字段", () => {
      const err = new IpcInvalidArgsError(
        "sidecar:probe",
        "(payload)",
        "object",
      );
      expect(ipcInvalidArgsLogFields(err, null)).toEqual({
        channel: "sidecar:probe",
        field: "(payload)",
        expected: "object",
        conversation_id: undefined,
        root_id: undefined,
      });
    });
  });
});
