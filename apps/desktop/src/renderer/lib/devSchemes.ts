import { type ComponentType, useSyncExternalStore } from "react";

/** 未选方案时挂载点用现行组件。 */
export const CURRENT_SCHEME_ID = "current";

export type DevScheme = {
  id: string;
  label: string;
  group: string;
  groupLabel: string;
  slots: Record<string, ComponentType<never>>;
};

type SchemeStore = Record<string, string>;

const STORAGE_KEY = "agentcore:dev-scheme";
const OPEN_KEY = "agentcore:dev-scheme-open";
const listeners = new Set<() => void>();
const schemes: DevScheme[] = [];
let snapshot: SchemeStore = readStore();
let revision = 0;
let focusedGroup: string | null = null;
let panelOpen = readPanelOpen();

function readStore(): SchemeStore {
  if (typeof sessionStorage === "undefined") return {};
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    const next: SchemeStore = {};
    for (const [group, id] of Object.entries(
      parsed as Record<string, unknown>,
    )) {
      if (typeof id === "string") next[group] = id;
    }
    return next;
  } catch {
    return {};
  }
}

function readPanelOpen(): boolean {
  if (typeof sessionStorage === "undefined") return false;
  return sessionStorage.getItem(OPEN_KEY) === "1";
}

function bump() {
  revision += 1;
  for (const listener of listeners) listener();
}

export function isSchemePanelOpen(): boolean {
  return panelOpen;
}

export function setSchemePanelOpen(open: boolean) {
  if (panelOpen === open) return;
  panelOpen = open;
  if (typeof sessionStorage !== "undefined") {
    if (open) sessionStorage.setItem(OPEN_KEY, "1");
    else sessionStorage.removeItem(OPEN_KEY);
  }
  bump();
}

export function useSchemePanelOpen(): boolean {
  useSyncExternalStore(subscribe, getRevision, getRevision);
  return panelOpen;
}

function persist(next: SchemeStore) {
  snapshot = next;
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }
  bump();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getRevision(): number {
  return revision;
}

export function registerScheme(scheme: DevScheme) {
  if (
    schemes.some((row) => row.group === scheme.group && row.id === scheme.id)
  ) {
    return;
  }
  schemes.push(scheme);
  bump();
}

export function listSchemes(group?: string): DevScheme[] {
  return group ? schemes.filter((row) => row.group === group) : [...schemes];
}

export function listSchemeGroups(): { id: string; label: string }[] {
  const seen = new Set<string>();
  const groups: { id: string; label: string }[] = [];
  for (const scheme of schemes) {
    if (seen.has(scheme.group)) continue;
    seen.add(scheme.group);
    groups.push({ id: scheme.group, label: scheme.groupLabel });
  }
  return groups;
}

export function getSchemeId(group: string): string {
  const id = snapshot[group] ?? CURRENT_SCHEME_ID;
  if (id === CURRENT_SCHEME_ID) return CURRENT_SCHEME_ID;
  return schemes.some((row) => row.group === group && row.id === id)
    ? id
    : CURRENT_SCHEME_ID;
}

export function setSchemeId(group: string, id: string) {
  focusedGroup = group;
  if (id === CURRENT_SCHEME_ID) {
    if (!(group in snapshot)) {
      bump();
      return;
    }
    const next = { ...snapshot };
    delete next[group];
    persist(next);
    return;
  }
  if (!schemes.some((row) => row.group === group && row.id === id)) return;
  if (snapshot[group] === id) return;
  persist({ ...snapshot, [group]: id });
}

export function focusSchemeGroup(group: string) {
  focusedGroup = group;
}

/** 循环当前组：现行 → 各档 → 现行。没有登记时是空操作。 */
export function cycleActiveSchemeGroup() {
  const groups = listSchemeGroups();
  if (groups.length === 0) return;
  const group =
    focusedGroup && groups.some((row) => row.id === focusedGroup)
      ? focusedGroup
      : groups[0].id;
  const ids = [CURRENT_SCHEME_ID, ...listSchemes(group).map((row) => row.id)];
  const current = getSchemeId(group);
  const index = ids.indexOf(current);
  const next = ids[(index + 1) % ids.length];
  if (next) setSchemeId(group, next);
  setSchemePanelOpen(true);
}

export function useSchemeId(group: string): string {
  useSyncExternalStore(subscribe, getRevision, getRevision);
  return getSchemeId(group);
}

export function useSchemeSlot<P extends object>(
  slot: string,
  Current: ComponentType<P>,
): ComponentType<P> {
  useSyncExternalStore(subscribe, getRevision, getRevision);
  for (const scheme of schemes) {
    if (!scheme.slots[slot]) continue;
    if (getSchemeId(scheme.group) !== scheme.id) continue;
    return scheme.slots[slot] as ComponentType<P>;
  }
  return Current;
}

/** 单测隔离；生产路径不要调用。 */
export function resetSchemesForTests() {
  schemes.length = 0;
  focusedGroup = null;
  snapshot = {};
  panelOpen = false;
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(OPEN_KEY);
  }
  bump();
}
