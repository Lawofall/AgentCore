import { XtermView } from "@/components/terminal/XtermView";
/**
 * 右坞「终端」tab —— M1 后台进程 + M2 执行记录 + M3 用户交互 shell。
 *
 * 纵向会话列表（无分区标题）+ 后台进程 / 执行记录；选中项看滚屏/交互。
 */
import { Button, ConfirmDialog, IconButton } from "@/components/ui";
import {
  type ExecutionRecord,
  deriveExecutionRecords,
  resolveRecordOutput,
} from "@/lib/executionRecords";
import {
  formatProcessDuration,
  shouldShowTerminalTab,
  stripAnsi,
} from "@/lib/processOutput";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";
import {
  type BackgroundProcessView,
  useBackgroundProcessStore,
} from "@/stores/backgroundProcesses";
import { useConversationStore } from "@/stores/conversation";
import { runtimeOf } from "@/stores/conversation/runtime";
import { useExecutionStore } from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { useToolOutputLiveStore } from "@/stores/toolOutputLive";
import {
  type UserTerminalView,
  isPtySessionBusy,
  useUserTerminalStore,
} from "@/stores/userTerminals";
import { ArrowUpRight, Plus, Square, Terminal, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Selection =
  | { kind: "pty"; id: string }
  | { kind: "process"; id: string }
  | { kind: "record"; id: string }
  | null;

export function TerminalPanelBody({
  preferredSessionId = null,
}: {
  /** 顶栏单壳 preferred：优先选中该 pty；无则走原有回落逻辑。 */
  preferredSessionId?: string | null;
}) {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const messages = useConversationStore(
    (s) => runtimeOf(s, conversationId).messages,
  );
  const executionById = useExecutionStore((s) => s.byId);

  const processes = useBackgroundProcessStore((s) =>
    s.processesFor(conversationId),
  );
  const selectProcess = useBackgroundProcessStore((s) => s.selectProcess);
  const stopProcess = useBackgroundProcessStore((s) => s.stopProcess);
  const hydrateConversation = useBackgroundProcessStore(
    (s) => s.hydrateConversation,
  );
  const ensureSubscribed = useBackgroundProcessStore((s) => s.ensureSubscribed);

  const sessions = useUserTerminalStore((s) => s.sessionsFor(conversationId));
  const selectSession = useUserTerminalStore((s) => s.selectSession);
  const spawnSession = useUserTerminalStore((s) => s.spawnSession);
  const killSession = useUserTerminalStore((s) => s.killSession);
  const writeInput = useUserTerminalStore((s) => s.writeInput);
  const resizeSession = useUserTerminalStore((s) => s.resize);
  const hydratePty = useUserTerminalStore((s) => s.hydrateConversation);
  const ensurePtySubscribed = useUserTerminalStore((s) => s.ensureSubscribed);

  const liveById = useToolOutputLiveStore((s) => s.byId);
  const selectRecord = useToolOutputLiveStore((s) => s.select);

  const records = useMemo(
    () => deriveExecutionRecords(messages, executionById),
    [messages, executionById],
  );

  const [selection, setSelection] = useState<Selection>(null);
  const [canOpenPty, setCanOpenPty] = useState(false);
  const [localTarget, setLocalTarget] = useState<{
    rootId: string;
    subpath: string;
  } | null>(null);
  const [spawnBusy, setSpawnBusy] = useState(false);
  const [spawnError, setSpawnError] = useState<string | null>(null);
  const [killConfirmId, setKillConfirmId] = useState<string | null>(null);
  const [killBusy, setKillBusy] = useState(false);

  useEffect(() => {
    ensureSubscribed();
    ensurePtySubscribed();
  }, [ensureSubscribed, ensurePtySubscribed]);

  useEffect(() => {
    if (conversationId) {
      void hydrateConversation(conversationId);
      void hydratePty(conversationId);
    }
  }, [conversationId, hydrateConversation, hydratePty]);

  useEffect(() => {
    let cancelled = false;
    if (!conversationId) {
      setCanOpenPty(false);
      setLocalTarget(null);
      return;
    }
    void resolveConversationLocalTarget(conversationId).then((t) => {
      if (cancelled) return;
      if (t) {
        setCanOpenPty(true);
        setLocalTarget(t);
      } else {
        setCanOpenPty(false);
        setLocalTarget(null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // 选中失效时回落：优先 preferredSession → 最新用户终端 → 进程 → 执行记录
  useEffect(() => {
    const ptyOk =
      selection?.kind === "pty" &&
      sessions.some((s) => s.session_id === selection.id);
    const processOk =
      selection?.kind === "process" &&
      processes.some((p) => p.process_id === selection.id);
    const recordOk =
      selection?.kind === "record" &&
      records.some((r) => r.id === selection.id);
    if (ptyOk || processOk || recordOk) return;

    if (
      preferredSessionId &&
      sessions.some((s) => s.session_id === preferredSessionId)
    ) {
      setSelection({ kind: "pty", id: preferredSessionId });
      selectSession(preferredSessionId);
      selectProcess(null);
      selectRecord(null);
      return;
    }

    const lastPty = sessions[sessions.length - 1];
    if (lastPty) {
      setSelection({ kind: "pty", id: lastPty.session_id });
      selectSession(lastPty.session_id);
      selectProcess(null);
      selectRecord(null);
      return;
    }
    const lastProc = processes[processes.length - 1];
    if (lastProc) {
      setSelection({ kind: "process", id: lastProc.process_id });
      selectProcess(lastProc.process_id);
      selectSession(null);
      selectRecord(null);
      return;
    }
    const lastRec = records[records.length - 1];
    if (lastRec) {
      setSelection({ kind: "record", id: lastRec.id });
      selectProcess(null);
      selectSession(null);
      selectRecord(lastRec.id);
      return;
    }
    setSelection(null);
  }, [
    selection,
    sessions,
    processes,
    records,
    preferredSessionId,
    selectProcess,
    selectRecord,
    selectSession,
  ]);

  const selectedPty =
    selection?.kind === "pty"
      ? (sessions.find((s) => s.session_id === selection.id) ?? null)
      : null;
  const selectedProcess =
    selection?.kind === "process"
      ? (processes.find((p) => p.process_id === selection.id) ?? null)
      : null;
  const selectedRecord =
    selection?.kind === "record"
      ? (records.find((r) => r.id === selection.id) ?? null)
      : null;

  const empty =
    sessions.length === 0 && processes.length === 0 && records.length === 0;

  const onSelectPty = (id: string) => {
    setSelection({ kind: "pty", id });
    selectSession(id);
    selectProcess(null);
    selectRecord(null);
  };
  const onSelectProcess = (id: string) => {
    setSelection({ kind: "process", id });
    selectProcess(id);
    selectSession(null);
    selectRecord(null);
  };
  const onSelectRecord = (id: string) => {
    setSelection({ kind: "record", id });
    selectRecord(id);
    selectProcess(null);
    selectSession(null);
  };

  const onSpawn = useCallback(async () => {
    if (!conversationId || !localTarget || spawnBusy) return;
    setSpawnBusy(true);
    setSpawnError(null);
    const result = await spawnSession({
      conversationId,
      rootId: localTarget.rootId,
      subpath: localTarget.subpath,
    });
    setSpawnBusy(false);
    if (!result.ok) {
      setSpawnError(result.detail);
      return;
    }
    setSelection({ kind: "pty", id: result.session_id });
    selectSession(result.session_id);
    selectProcess(null);
    selectRecord(null);
  }, [
    conversationId,
    localTarget,
    spawnBusy,
    spawnSession,
    selectSession,
    selectProcess,
    selectRecord,
  ]);

  const finishKillSession = useCallback(
    async (sessionId: string) => {
      setKillBusy(true);
      const ok = await killSession(sessionId);
      setKillBusy(false);
      if (!ok) return;
      useSidePanelStore.getState().clearTerminalPreferredSession(sessionId);
      setKillConfirmId(null);
    },
    [killSession],
  );

  const onClosePty = useCallback(
    (session: UserTerminalView) => {
      if (isPtySessionBusy(session)) {
        setKillConfirmId(session.session_id);
        return;
      }
      void finishKillSession(session.session_id);
    },
    [finishKillSession],
  );

  const live = selectedRecord ? liveById[selectedRecord.id] : undefined;
  const recordOutput = selectedRecord
    ? resolveRecordOutput(
        selectedRecord,
        live?.stdout ?? "",
        live?.stderr ?? "",
      )
    : "";

  const showPtySection = canOpenPty || sessions.length > 0;
  const runningPtyCount = sessions.filter((s) => s.status === "running").length;
  const canSpawnMore =
    canOpenPty && runningPtyCount < 3 && Boolean(localTarget);

  if (empty && !canOpenPty) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
        <Terminal size={20} className="text-muted-foreground/60" />
        <p>暂无终端活动</p>
        <p className="text-xs">后台进程与代码/测试执行会显示在这里</p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="max-h-48 shrink-0 space-y-2 overflow-y-auto border-b border-border p-2">
        {showPtySection && (
          <section>
            <div className="flex items-center gap-1 px-1 pb-0.5">
              {sessions.length === 0 && canOpenPty ? (
                <p className="min-w-0 flex-1 truncate px-1 text-xs text-muted-foreground">
                  点 + 打开终端
                </p>
              ) : (
                <div className="min-w-0 flex-1" />
              )}
              {canSpawnMore && (
                <IconButton
                  size="sm"
                  onClick={() => void onSpawn()}
                  disabled={spawnBusy}
                  aria-label="新开终端"
                  title="新开终端"
                >
                  <Plus size={14} />
                </IconButton>
              )}
            </div>
            {spawnError ? (
              <p className="px-2 pb-1 text-xs text-destructive">{spawnError}</p>
            ) : null}
            {sessions.length > 0 && (
              <ul className="space-y-0.5">
                {sessions.map((s) => (
                  <PtyRow
                    key={s.session_id}
                    session={s}
                    active={
                      selection?.kind === "pty" && selection.id === s.session_id
                    }
                    onSelect={() => onSelectPty(s.session_id)}
                    onClose={() => onClosePty(s)}
                  />
                ))}
              </ul>
            )}
          </section>
        )}
        {processes.length > 0 && (
          <section>
            <h3 className="px-2 pb-1 text-xs text-muted-foreground">
              后台进程
            </h3>
            <ul className="space-y-0.5">
              {processes.map((p) => (
                <ProcessRow
                  key={p.process_id}
                  process={p}
                  active={
                    selection?.kind === "process" &&
                    selection.id === p.process_id
                  }
                  onSelect={() => onSelectProcess(p.process_id)}
                  onStop={() => void stopProcess(p.process_id)}
                />
              ))}
            </ul>
          </section>
        )}
        {records.length > 0 && (
          <section>
            <h3 className="px-2 pb-1 text-xs text-muted-foreground">
              执行记录
            </h3>
            <ul className="space-y-0.5">
              {records.map((r) => (
                <RecordRow
                  key={r.id}
                  record={r}
                  live={liveById[r.id]}
                  active={selection?.kind === "record" && selection.id === r.id}
                  onSelect={() => onSelectRecord(r.id)}
                />
              ))}
            </ul>
          </section>
        )}
      </div>
      {selectedPty ? (
        <PtyOutput
          session={selectedPty}
          onData={(data) => writeInput(selectedPty.session_id, data)}
          onResize={(cols, rows) =>
            resizeSession(selectedPty.session_id, cols, rows)
          }
        />
      ) : selectedProcess ? (
        <ProcessOutput process={selectedProcess} />
      ) : selectedRecord ? (
        <RecordOutput
          text={recordOutput}
          running={selectedRecord.status === "running"}
        />
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          {canOpenPty ? "新开或选择一项查看" : "选择一项查看输出"}
        </div>
      )}
      <ConfirmDialog
        open={killConfirmId != null}
        onOpenChange={(next) => {
          if (!next && !killBusy) setKillConfirmId(null);
        }}
        title="要终止正在运行的进程吗？"
        description="关闭将终止此终端中的进程"
        confirmLabel="终止"
        tone="danger"
        busy={killBusy}
        onConfirm={() => {
          if (killConfirmId) void finishKillSession(killConfirmId);
        }}
      />
    </div>
  );
}

function PtyRow({
  session: s,
  active,
  onSelect,
  onClose,
}: {
  session: UserTerminalView;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (s.status !== "running") return;
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [s.status]);

  const duration = formatProcessDuration(s.started_at);

  return (
    <li>
      <div
        className={`group flex items-center gap-2 rounded-lg px-2 py-1.5 ${
          active ? "bg-accent text-foreground" : "hover:bg-accent/50"
        }`}
      >
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <StatusDot
            status={s.status === "running" ? "running" : "done"}
            failed={
              s.status === "exited" && s.exit_code != null && s.exit_code !== 0
            }
          />
          <span className="min-w-0 flex-1 truncate text-sm" title={s.shell}>
            {s.name}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {duration}
          </span>
        </button>
        <IconButton
          size="sm"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          aria-label="关闭终端"
          className="size-6 text-muted-foreground hover:text-destructive"
        >
          <X size={12} />
        </IconButton>
      </div>
    </li>
  );
}

function ProcessRow({
  process: p,
  active,
  onSelect,
  onStop,
}: {
  process: BackgroundProcessView;
  active: boolean;
  onSelect: () => void;
  onStop: () => void;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (p.status !== "running") return;
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [p.status]);

  const label = p.name?.trim() || p.command;
  const duration = formatProcessDuration(p.started_at);

  return (
    <li>
      <div
        className={`group flex items-center gap-2 rounded-lg px-2 py-1.5 ${
          active ? "bg-accent text-foreground" : "hover:bg-accent/50"
        }`}
      >
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <StatusDot
            status={p.status === "running" ? "running" : "done"}
            failed={
              p.status === "exited" && p.exit_code != null && p.exit_code !== 0
            }
          />
          <span className="min-w-0 flex-1 truncate text-sm" title={p.command}>
            {label}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {duration}
          </span>
        </button>
        {p.status === "running" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              onStop();
            }}
            className="h-7 shrink-0 gap-1 px-2 text-xs text-muted-foreground hover:text-destructive"
            icon={<Square size={12} />}
            aria-label="停止进程"
          >
            停止
          </Button>
        )}
      </div>
    </li>
  );
}

function RecordRow({
  record: r,
  live,
  active,
  onSelect,
}: {
  record: ExecutionRecord;
  live?: { startedAt: string; endedAt?: string };
  active: boolean;
  onSelect: () => void;
}) {
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const [, setTick] = useState(0);
  const startedAt = live?.startedAt;
  useEffect(() => {
    if (r.status !== "running" || !startedAt) return;
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [r.status, startedAt]);

  const duration = startedAt
    ? formatProcessDuration(
        startedAt,
        live?.endedAt ? Date.parse(live.endedAt) : Date.now(),
      )
    : "—";
  const toolLabel =
    r.toolName === "test_run"
      ? "测试"
      : r.toolName === "code_execute"
        ? "执行"
        : r.toolName;

  return (
    <li>
      <div
        className={`group flex items-center gap-2 rounded-lg px-2 py-1.5 ${
          active ? "bg-accent text-foreground" : "hover:bg-accent/50"
        }`}
      >
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <StatusDot
            status={r.status === "running" ? "running" : "done"}
            failed={r.status === "error"}
          />
          <span className="min-w-0 flex-1 truncate text-sm" title={r.summary}>
            <span className="text-muted-foreground">{toolLabel}</span>
            <span className="mx-1 text-muted-foreground/50">·</span>
            {r.summary}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {r.agentRole}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
            {duration}
          </span>
        </button>
        {r.runId ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              showRunDetail(r.messageId, r.runId as string, r.agentRole);
            }}
            className="h-7 shrink-0 gap-1 px-2 text-xs text-muted-foreground"
            icon={<ArrowUpRight size={12} />}
            aria-label="跳到 run 详情"
          >
            详情
          </Button>
        ) : null}
      </div>
    </li>
  );
}

function StatusDot({
  status,
  failed,
}: {
  status: "running" | "done";
  failed?: boolean;
}) {
  const cls =
    status === "running"
      ? "bg-primary"
      : failed
        ? "bg-destructive"
        : "bg-success";
  return (
    <span
      className={`size-2 shrink-0 rounded-full ${cls} ${
        status === "running" ? "animate-pulse motion-reduce:animate-none" : ""
      }`}
      aria-hidden
    />
  );
}

function PtyOutput({
  session,
  onData,
  onResize,
}: {
  session: UserTerminalView;
  onData: (data: string) => void;
  onResize: (cols: number, rows: number) => void;
}) {
  return (
    <XtermView
      sessionId={session.session_id}
      output={session.output}
      writable={session.status === "running"}
      onData={onData}
      onResize={onResize}
    />
  );
}

function ProcessOutput({
  process: p,
}: {
  process: BackgroundProcessView | null;
}) {
  return <ScrollOutput text={p ? stripAnsi(p.output) || "（尚无输出）" : ""} />;
}

function RecordOutput({ text, running }: { text: string; running: boolean }) {
  const body = text || (running ? "（执行中…）" : "（无输出）");
  return <ScrollOutput text={body} />;
}

function ScrollOutput({ text }: { text: string }) {
  const preRef = useRef<HTMLPreElement>(null);
  const stickRef = useRef(true);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll when output grows
  useEffect(() => {
    const el = preRef.current;
    if (!el || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [text]);

  return (
    <pre
      ref={preRef}
      onScroll={(e) => {
        const el = e.currentTarget;
        stickRef.current =
          el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
      className="min-h-0 flex-1 overflow-auto bg-muted/30 p-3 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap break-all"
    >
      {text}
    </pre>
  );
}

/**
 * Drive 终端 tab 自动浮出：仅当本对话确有后台进程 / 执行记录 / 用户终端。
 * 「可开交互 shell」只影响面板内空态，不强迫挂顶栏 tab（否则用户关了会立刻被加回）。
 */
export function useTerminalRegion(): { show: boolean } {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const messages = useConversationStore(
    (s) => runtimeOf(s, conversationId).messages,
  );
  const executionById = useExecutionStore((s) => s.byId);
  const ensureSubscribed = useBackgroundProcessStore((s) => s.ensureSubscribed);
  const hydrateConversation = useBackgroundProcessStore(
    (s) => s.hydrateConversation,
  );
  const processCount = useBackgroundProcessStore(
    (s) => s.processesFor(conversationId).length,
  );
  const ptyCount = useUserTerminalStore(
    (s) => s.sessionsFor(conversationId).length,
  );
  const ensurePty = useUserTerminalStore((s) => s.ensureSubscribed);
  const hydratePty = useUserTerminalStore((s) => s.hydrateConversation);

  const recordCount = useMemo(
    () => deriveExecutionRecords(messages, executionById).length,
    [messages, executionById],
  );

  const show = shouldShowTerminalTab(
    processCount,
    recordCount,
    ptyCount,
    false,
  );

  useEffect(() => {
    ensureSubscribed();
    ensurePty();
  }, [ensureSubscribed, ensurePty]);

  useEffect(() => {
    if (conversationId) {
      void hydrateConversation(conversationId);
      void hydratePty(conversationId);
    }
  }, [conversationId, hydrateConversation, hydratePty]);

  return { show };
}
