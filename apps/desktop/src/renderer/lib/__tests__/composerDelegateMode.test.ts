// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  __resetComposerDelegateModeForTests,
  clearComposerDraftDelegateMode,
  getComposerDraftDelegateMode,
  rememberDelegateSelection,
  setComposerDraftDelegateMode,
  subscribeComposerDraftDelegateMode,
} from "../composerDelegateMode";

describe("composerDelegateMode", () => {
  beforeEach(() => {
    __resetComposerDelegateModeForTests();
  });

  afterEach(() => {
    __resetComposerDelegateModeForTests();
  });

  it("草稿默认关，订阅能跟上拨动", () => {
    const seen: boolean[] = [];
    const unsub = subscribeComposerDraftDelegateMode(() => {
      seen.push(getComposerDraftDelegateMode());
    });
    setComposerDraftDelegateMode(true);
    setComposerDraftDelegateMode(true);
    setComposerDraftDelegateMode(false);
    unsub();
    expect(seen).toEqual([true, false]);
    expect(getComposerDraftDelegateMode()).toBe(false);
  });

  it("清空草稿覆盖后仍跟上次选用", () => {
    setComposerDraftDelegateMode(true);
    clearComposerDraftDelegateMode();
    expect(getComposerDraftDelegateMode()).toBe(true);
  });

  it("从没拨过默认关", () => {
    expect(getComposerDraftDelegateMode()).toBe(false);
  });

  it("这场记下的选用能种空草稿", () => {
    rememberDelegateSelection(true);
    expect(getComposerDraftDelegateMode()).toBe(true);
  });
});
