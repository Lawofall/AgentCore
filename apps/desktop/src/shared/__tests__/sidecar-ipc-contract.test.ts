import sidecarIpc from "@agentcore/contract-types/sidecar-ipc.json";
import {
  type SidecarConversationSnapshot,
  type SidecarResumeRequest,
  type SidecarTurnResult,
  bareSidecarSession,
  buildSidecarResumeRpcParams,
} from "@shared/sidecar-contract";
import { describe, expect, it } from "vitest";

/** Runtime key guard — compile-time ``SidecarTurnResult`` + contract JSON must agree. */
function assertExactKeys(obj: object, keys: readonly string[]): void {
  expect(Object.keys(obj).sort()).toEqual([...keys].sort());
}

const BARE_SESSION: SidecarConversationSnapshot = bareSidecarSession();

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
      | "session"
    > = {
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: ["a"],
      session: {
        ...BARE_SESSION,
        permissionAxes: { boundary: "read" },
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
      {
        baseUrl: "https://api.example.com/v1/workspaces",
        apiKey: "workspaces-tok",
      },
    );
    // Optional auth keys omitted when empty — exact-key sample excludes userId.
    assertExactKeys(withInference, [
      ...sidecarIpc.resumeRpcParams.keys.filter((k) => k !== "userId"),
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
    expect(withInference.workspacesAuth).toEqual({
      baseUrl: "https://api.example.com/v1/workspaces",
      apiKey: "workspaces-tok",
    });

    const withoutInference = buildSidecarResumeRpcParams(req);
    assertExactKeys(withoutInference, [
      ...sidecarIpc.resumeRpcParams.keys.filter(
        (k) =>
          k !== "inference" &&
          k !== "foldersAuth" &&
          k !== "accountAuth" &&
          k !== "workspacesAuth" &&
          k !== "browserBridge" &&
          k !== "userId",
      ),
    ]);
    expect(withoutInference.selected).toEqual(["a"]);
    expect(withoutInference.session).toEqual({
      ...BARE_SESSION,
      permissionAxes: { boundary: "read" },
    });

    // Explicit null clears sidecar sticky env / prior turn.
    const clearBridge = buildSidecarResumeRpcParams(req, undefined, null);
    expect(clearBridge.browserBridge).toBeNull();

    const withDelegate = buildSidecarResumeRpcParams({
      ...req,
      session: { ...BARE_SESSION, delegateMode: true },
    });
    expect(
      (withDelegate.session as SidecarConversationSnapshot).delegateMode,
    ).toBe(true);
    const delegateOff = buildSidecarResumeRpcParams({
      ...req,
      session: { ...BARE_SESSION, delegateMode: false },
    });
    expect(
      (delegateOff.session as SidecarConversationSnapshot).delegateMode,
    ).toBe(false);
  });

  it("buildSidecarResumeRpcParams 可选透传 userId", () => {
    const withUser = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
      session: BARE_SESSION,
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
      session: BARE_SESSION,
    });
    expect(withoutUser.userId).toBeUndefined();
    expect("userId" in withoutUser).toBe(false);
  });

  it("buildSidecarResumeRpcParams always includes session snapshot", () => {
    const omitted = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
      session: BARE_SESSION,
    });
    expect(omitted.session).toEqual(BARE_SESSION);
    expect(omitted).not.toHaveProperty("rootId");
    expect(omitted).not.toHaveProperty("subpath");

    const bound = buildSidecarResumeRpcParams({
      messageId: "m-asst",
      conversationId: "c1",
      traceId: "abc123",
      decision: "continue",
      note: "",
      selected: [],
      session: {
        ...BARE_SESSION,
        folderId: "fold-1",
        localRootId: "root-1",
        localSubpath: "src",
      },
    });
    expect(bound.session).toEqual({
      ...BARE_SESSION,
      folderId: "fold-1",
      localRootId: "root-1",
      localSubpath: "src",
    });
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

  it("workspacesAuth block keys align with SidecarWorkspacesAuth", () => {
    const workspacesAuth = {
      baseUrl: "https://x/v1/workspaces",
      apiKey: "k",
    };
    assertExactKeys(workspacesAuth, sidecarIpc.workspacesAuth.keys);
    expect(sidecarIpc.workspacesAuth.required).toEqual(
      sidecarIpc.workspacesAuth.keys,
    );
  });
});
