import { api } from "@/services/api";

/** One envelope projection on the assembly page. */
export interface EnvelopeSwitchRow {
  id: string;
  label: string;
  summary: string;
  off: boolean;
}

export interface EnvelopeSwitchboard {
  omitted: string[];
  switches: EnvelopeSwitchRow[];
}

type Listener = () => void;

/** Blank composer only. Null means「还没改过」, create leaves every projection on. */
let composerDraftOmitted: string[] | null = null;
const draftListeners = new Set<Listener>();

function publishDraft(): void {
  for (const listener of draftListeners) listener();
}

export function setComposerDraftOmittedProjections(
  ids: readonly string[] | null,
): void {
  if (ids == null) {
    if (composerDraftOmitted == null) return;
    composerDraftOmitted = null;
  } else {
    composerDraftOmitted = [...ids];
  }
  publishDraft();
}

export function getComposerDraftOmittedProjections(): string[] | null {
  return composerDraftOmitted;
}

export function peekComposerDraftOmittedProjections(): string[] | null {
  return composerDraftOmitted == null ? null : [...composerDraftOmitted];
}

export function subscribeComposerDraftOmittedProjections(
  listener: Listener,
): () => void {
  draftListeners.add(listener);
  return () => draftListeners.delete(listener);
}

export function __resetEnvelopeSwitchStoresForTests(): void {
  composerDraftOmitted = null;
}

export async function loadAccountEnvelopeSwitches(): Promise<EnvelopeSwitchboard> {
  return api.get<EnvelopeSwitchboard>("/v1/users/me/envelope-switches");
}

export async function saveAccountEnvelopeSwitches(
  omitted: string[],
): Promise<EnvelopeSwitchboard> {
  return api.put<EnvelopeSwitchboard>("/v1/users/me/envelope-switches", {
    omitted,
  });
}

export async function loadConversationEnvelopeSwitches(
  conversationId: string,
): Promise<EnvelopeSwitchboard> {
  return api.get<EnvelopeSwitchboard>(
    `/v1/conversations/${conversationId}/envelope-switches`,
  );
}

export async function saveConversationEnvelopeSwitches(
  conversationId: string,
  omitted: string[],
): Promise<EnvelopeSwitchboard> {
  return api.put<EnvelopeSwitchboard>(
    `/v1/conversations/${conversationId}/envelope-switches`,
    { omitted },
  );
}

/** Next omit list after flipping one projection. `on` means the line stays. */
export function omittedAfterToggle(
  omitted: readonly string[],
  id: string,
  on: boolean,
): string[] {
  const next = new Set(omitted);
  if (on) next.delete(id);
  else next.add(id);
  return [...next];
}

/**
 * Every projection on, before a conversation exists.
 * Ids match ``PROJECTIONS`` in ``runtime/context/envelope_switches.py``.
 */
export function draftEnvelopeBoard(): EnvelopeSwitchboard {
  return {
    omitted: [],
    switches: [
      {
        id: "runtime_date",
        label: "日期",
        summary: "信封里报当前日期。主管和队员读同一场。",
        off: false,
      },
      {
        id: "execution",
        label: "执行",
        summary: "信封报云端还是本机、出站、原件能不能改。",
        off: false,
      },
      {
        id: "boundary",
        label: "边界",
        summary: "信封报只看、这个文件夹或这台电脑。关掉不改这道边界。",
        off: false,
      },
      {
        id: "desk",
        label: "桌",
        summary: "信封报坐在哪个文件夹或本会话草稿。",
        off: false,
      },
      {
        id: "system",
        label: "系统",
        summary: "信封报操作系统和壳。",
        off: false,
      },
      {
        id: "git",
        label: "Git",
        summary: "信封报工作区根有没有仓库、在哪条分支。",
        off: false,
      },
      {
        id: "client",
        label: "客户端",
        summary: "信封报桌面连没连上。本机工具已在表里时这行本来就空。",
        off: false,
      },
      {
        id: "mounts",
        label: "区外",
        summary: "信封报挂进来的区外目录和能不能改。",
        off: false,
      },
      {
        id: "gaps",
        label: "缺口",
        summary: "信封报边界允许、但这场没装上的名字。",
        off: false,
      },
      {
        id: "sandbox",
        label: "沙箱",
        summary: "云端跑命令不可用时，信封报原因。",
        off: false,
      },
      {
        id: "interpreters",
        label: "解释器",
        summary: "本机解释器没配齐时，信封报缺哪些。",
        off: false,
      },
      {
        id: "file_index",
        label: "文件索引",
        summary: "主管信封附上文件名单。队员不读这份名单。",
        off: false,
      },
    ],
  };
}
