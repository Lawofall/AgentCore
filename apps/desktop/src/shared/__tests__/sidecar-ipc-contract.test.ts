import sidecarIpc from "@agentcore/contract-types/sidecar-ipc.json";
import {
  type SidecarResumeRequest,
  type SidecarTurnResult,
  buildSidecarResumeRpcParams,
} from "@shared/sidecar-contract";
import { describe, expect, it } from "vitest";

/** Runtime key guard — compile-time ``SidecarTurnResult`` + contract JSON must agree. */
function assertExactKeys(obj: object, keys: readonly string[]): void {
  expect(Object.keys(obj).sort()).toEqual([...keys].sort());
}

describe("sidecar IPC contract (TS ↔ Python single source)", () => {
  it("SidecarTurnResult sample matches turnResult.keys + usageKeys", () => {
    const sample: SidecarTurnResult = {
      turnId: "t1",
      messageId: "m1",
      content: "hi",
      reasoningContent: null,
      finishReason: "stop",
      model: "deepseek-v4-flash",
      rounds: 1,
      usage: {
        inputTokens: 10,
        outputTokens: 5,
        reasoningTokens: 0,
        cacheHitTokens: 0,
        cacheMissTokens: 0,
      },
      citations: [],
      runs: null,
      error: null,
    };
    assertExactKeys(sample, sidecarIpc.turnResult.keys);
    assertExactKeys(sample.usage, sidecarIpc.turnResult.usageKeys);
  });

  it("buildSidecarResumeRpcParams emits exactly resumeRpcParams.keys", () => {
    const req: Pick<
      SidecarResumeRequest,
      | "messageId"
      | "conversationId"
      | "traceId"
      | "decision"
      | "note"
      | "selected"
      | "permissionAxes"
    > = {
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: ["a"],
      permissionAxes: {
        file_write: "ask",
        command: "ask",
        host: "off",
      },
    };
    const withInference = buildSidecarResumeRpcParams(
      req,
      {
        baseUrl: "https://x/v1/inference/v1",
        apiKey: "tok",
        model: "deepseek-v4-flash",
      },
      { baseUrl: "http://127.0.0.1:9", token: "bridge-tok" },
      { baseUrl: "https://api.example.com", apiKey: "folders-tok" },
      { baseUrl: "https://api.example.com/v1/account", apiKey: "account-tok" },
    );
    // Optional team_preview veto keys are omitted when empty (same as absent
    // inference / browserBridge / foldersAuth / accountAuth) — exact-key sample excludes them.
    assertExactKeys(withInference, [
      ...sidecarIpc.resumeRpcParams.keys.filter(
        (k) =>
          k !== "excluded_run_ids" &&
          k !== "write_capability_overrides" &&
          k !== "model_overrides" &&
          k !== "userId",
      ),
    ]);
    expect(withInference.browserBridge).toEqual({
      baseUrl: "http://127.0.0.1:9",
      token: "bridge-tok",
    });
    expect(withInference.foldersAuth).toEqual({
      baseUrl: "https://api.example.com",
      apiKey: "folders-tok",
    });
    expect(withInference.accountAuth).toEqual({
      baseUrl: "https://api.example.com/v1/account",
      apiKey: "account-tok",
    });

    const withoutInference = buildSidecarResumeRpcParams(req);
    assertExactKeys(withoutInference, [
      ...sidecarIpc.resumeRpcParams.keys.filter(
        (k) =>
          k !== "inference" &&
          k !== "foldersAuth" &&
          k !== "accountAuth" &&
          k !== "browserBridge" &&
          k !== "excluded_run_ids" &&
          k !== "write_capability_overrides" &&
          k !== "model_overrides" &&
          k !== "userId",
      ),
    ]);
    expect(withoutInference.selected).toEqual(["a"]);
    expect(withoutInference.permissionAxes).toEqual({
      file_write: "ask",
      command: "ask",
      host: "off",
    });

    // Explicit null clears sidecar sticky env / prior turn.
    const clearBridge = buildSidecarResumeRpcParams(req, undefined, null);
    expect(clearBridge.browserBridge).toBeNull();

    // 权限轴缺省 ⇒ 键不出现，sidecar 沿用当前值。
    const withoutAxes = buildSidecarResumeRpcParams({
      ...req,
      permissionAxes: undefined,
    });
    expect("permissionAxes" in withoutAxes).toBe(false);
  });

  it("buildSidecarResumeRpcParams 可选透传 excluded_run_ids / write_capability_overrides", () => {
    const params = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
      excluded_run_ids: ["r2"],
      write_capability_overrides: [{ run_id: "r1", capability: "text_only" }],
    });
    expect(params.excluded_run_ids).toEqual(["r2"]);
    expect(params.write_capability_overrides).toEqual([
      { run_id: "r1", capability: "text_only" },
    ]);
    expect(params.messageId).toBe("m-asst");
    expect(params.decision).toBe("continue");
    expect(params.selected).toEqual([]);
    for (const key of Object.keys(params)) {
      expect(sidecarIpc.resumeRpcParams.keys).toContain(key);
    }
  });

  it("buildSidecarResumeRpcParams 可选透传 userId", () => {
    const withUser = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
      userId: "acct-uuid-1",
    });
    expect(withUser.userId).toBe("acct-uuid-1");
    expect(sidecarIpc.resumeRpcParams.keys).toContain("userId");

    const withoutUser = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
    });
    expect(withoutUser.userId).toBeUndefined();
    expect("userId" in withoutUser).toBe(false);
  });

  it("buildSidecarResumeRpcParams always includes folderId and localRootId/localSubpath (null default)", () => {
    const omitted = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
    });
    expect(omitted.folderId).toBeNull();
    expect(omitted.localRootId).toBeNull();
    expect(omitted.localSubpath).toBeNull();
    expect(omitted).not.toHaveProperty("rootId");
    expect(omitted).not.toHaveProperty("subpath");

    const bound = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
      folderId: "fold-1",
      localRootId: "root-1",
      localSubpath: "src",
    });
    expect(bound.folderId).toBe("fold-1");
    expect(bound.localRootId).toBe("root-1");
    expect(bound.localSubpath).toBe("src");
  });

  it("resume IPC request required fields are a superset of renderer routing keys", () => {
    const ipcOnly = sidecarIpc.resumeIpcRequest.keys.filter(
      (k) => !sidecarIpc.resumeRpcParams.keys.includes(k),
    );
    expect(ipcOnly.sort()).toEqual(["rootId", "subpath"]);
    for (const key of sidecarIpc.resumeRpcParams.required) {
      expect(sidecarIpc.resumeIpcRequest.required).toContain(key);
    }
  });

  it("write-back maps every SidecarTurnResult persistence field to RecordTurnRequest", () => {
    const map = sidecarIpc.writeBack.resultToRecordTurn;
    const resultKeys = new Set<string>();
    for (const from of Object.keys(map)) {
      if (from.startsWith("usage.")) {
        resultKeys.add("usage");
      } else {
        resultKeys.add(from);
      }
    }
    const persistable = sidecarIpc.turnResult.keys.filter(
      (k) => !sidecarIpc.writeBack.ipcOnlyTurnResultFields.includes(k),
    );
    expect([...resultKeys].sort()).toEqual([...persistable].sort());
    expect(sidecarIpc.writeBack.contextFields).toEqual({
      traceId: "trace_id",
      userMessage: "user_message",
      optimisticUserId: "user_message_id",
      agentMentions: "agent_mentions",
    });
  });

  it("inference block keys align with SidecarInference", () => {
    const inference = {
      baseUrl: "https://x",
      apiKey: "k",
      model: "m",
    };
    assertExactKeys(inference, sidecarIpc.inference.keys);
    expect(sidecarIpc.inference.required).toEqual(sidecarIpc.inference.keys);
  });

  it("foldersAuth block keys align with SidecarFoldersAuth", () => {
    const foldersAuth = {
      baseUrl: "https://x",
      apiKey: "k",
    };
    assertExactKeys(foldersAuth, sidecarIpc.foldersAuth.keys);
    expect(sidecarIpc.foldersAuth.required).toEqual(
      sidecarIpc.foldersAuth.keys,
    );
  });

  it("accountAuth block keys align with SidecarAccountAuth", () => {
    const accountAuth = {
      baseUrl: "https://x/v1/account",
      apiKey: "k",
    };
    assertExactKeys(accountAuth, sidecarIpc.accountAuth.keys);
    expect(sidecarIpc.accountAuth.required).toEqual(
      sidecarIpc.accountAuth.keys,
    );
  });
});
