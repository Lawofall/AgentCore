// @vitest-environment jsdom
import {
  openDraftConversation,
  startNewConversation,
} from "@/lib/newConversation";
import { useConversationStore } from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import { useSidePanelStore } from "@/stores/sidePanel";
import { beforeEach, describe, expect, it, vi } from "vitest";

describe("openDraftConversation / startNewConversation", () => {
  beforeEach(() => {
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "quick_cloud" },
    });
    useConversationStore.setState({ currentConversationId: "old-conv" });
    useSidePanelStore.setState({ open: true });
    window.location.hash = "#/conversations/old-conv";
  });

  it("openDraftConversation: project intent + close dock + draft + hash /", () => {
    openDraftConversation("folder-new");
    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "folder-new",
    });
    expect(useSidePanelStore.getState().open).toBe(false);
    expect(useConversationStore.getState().currentConversationId).toBeNull();
    expect(window.location.hash).toBe("#/");
  });

  it("startNewConversation: same store side-effects, uses navigate not hash", () => {
    const navigate = vi.fn();
    window.location.hash = "#/conversations/old-conv";
    startNewConversation(navigate, "folder-nav");
    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "folder-nav",
    });
    expect(useSidePanelStore.getState().open).toBe(false);
    expect(useConversationStore.getState().currentConversationId).toBeNull();
    expect(navigate).toHaveBeenCalledWith("/");
    // navigate path does not rewrite hash itself
    expect(window.location.hash).toBe("#/conversations/old-conv");
  });
});
