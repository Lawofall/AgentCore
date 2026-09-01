// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useLlmModelProfiles", () => ({
  useLlmModelProfiles: vi.fn(),
}));
vi.mock("@/hooks/useModels", () => ({ useModels: vi.fn() }));
vi.mock("@/hooks/useConversations", () => ({
  useConversations: vi.fn(() => []),
}));

import { useConversations } from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useModels } from "@/hooks/useModels";
import {
  COMPOSER_VISION_HINT,
  useComposerProfileDraftStore,
} from "@/lib/composerModelProfile";
import type { LlmModelProfileListResponse } from "@/services/llmModelProfiles";
import { useComposerDraftStore } from "@/stores/composer";
import { useConversationStore } from "@/stores/conversation";
import type { Conversation } from "@/stores/conversation";
import { ComposerVisionHint } from "../ComposerVisionHint";

const useProfilesMock = vi.mocked(useLlmModelProfiles);
const useModelsMock = vi.mocked(useModels);
const useConversationsMock = vi.mocked(useConversations);

const textProfile = {
  id: "sys-52",
  name: "Flash",
  kind: "system" as const,
  is_default: true,
  main: {
    origin: "byok" as const,
    provider_id: "p1",
    model: "deepseek-v4-flash",
  },
  worker: null,
  background: null,
  vision: null,
};

const visionProfile = {
  ...textProfile,
  id: "user-vision",
  name: "看图",
  kind: "user" as const,
  is_default: false,
  main: { origin: "byok" as const, provider_id: "p2", model: "gpt-4o" },
};

const slottedProfile = {
  ...textProfile,
  id: "user-slot",
  name: "识图槽",
  kind: "user" as const,
  is_default: false,
  vision: { origin: "byok" as const, provider_id: "p2", model: "gpt-4o" },
};

function profiles(
  rows: LlmModelProfileListResponse["data"] = [textProfile],
): LlmModelProfileListResponse {
  return {
    default_model_profile_id: rows[0]?.id ?? "sys-52",
    data: rows,
  };
}

const png = {
  id: "a1",
  key: "file:local:pic.png",
  name: "pic.png",
  path: "pic.png",
  text: "",
  truncated: false,
  kind: "file" as const,
  binary: true,
};

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useConversationsMock.mockReturnValue([]);
  useComposerProfileDraftStore.setState({ profileId: null });
  useComposerDraftStore.setState({
    drafts: {},
    fillToken: 0,
    dockFlipToken: 0,
  });
  useProfilesMock.mockReturnValue({
    data: profiles(),
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useLlmModelProfiles>);
  useModelsMock.mockReturnValue({
    data: {
      byok_configured: true,
      current: { id: "deepseek-v4-flash", origin: "byok" },
      models: [
        {
          id: "deepseek-v4-flash",
          origin: "byok",
          display_name: "Flash",
          vendor: "DeepSeek",
          provider_id: "p1",
          capabilities: [],
          available: true,
        },
        {
          id: "gpt-4o",
          origin: "byok",
          display_name: "GPT-4o",
          vendor: "OpenAI",
          provider_id: "p2",
          capabilities: ["vision"],
          available: true,
        },
      ],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useModels>);
});

afterEach(cleanup);

describe("ComposerVisionHint", () => {
  it("有图 + 文本主且无槽 → 展示轻提示", () => {
    useComposerDraftStore.getState().setAttachments("__draft__", [png]);
    render(<ComposerVisionHint />);
    expect(screen.getByTestId("composer-vision-hint").textContent).toBe(
      COMPOSER_VISION_HINT,
    );
  });

  it("无图 → 不展示", () => {
    render(<ComposerVisionHint />);
    expect(screen.queryByTestId("composer-vision-hint")).toBeNull();
  });

  it("主模型 catalog 带 vision → 不展示", () => {
    useComposerDraftStore.getState().setAttachments("__draft__", [png]);
    useProfilesMock.mockReturnValue({
      data: profiles([visionProfile, textProfile]),
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useLlmModelProfiles>);
    useComposerProfileDraftStore.getState().setProfileId("user-vision");
    render(<ComposerVisionHint />);
    expect(screen.queryByTestId("composer-vision-hint")).toBeNull();
  });

  it("识图槽已填 → 不展示", () => {
    useComposerDraftStore.getState().setAttachments("__draft__", [png]);
    useProfilesMock.mockReturnValue({
      data: profiles([slottedProfile, textProfile]),
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useLlmModelProfiles>);
    useComposerProfileDraftStore.getState().setProfileId("user-slot");
    render(<ComposerVisionHint />);
    expect(screen.queryByTestId("composer-vision-hint")).toBeNull();
  });

  it("会话钉住的组合优先于账号默认", () => {
    useConversationStore.setState({
      currentConversationId: "c1",
      byId: {},
    });
    useConversationsMock.mockReturnValue([
      { id: "c1", modelProfileId: "user-vision" } as Conversation,
    ]);
    useComposerDraftStore.getState().setAttachments("c1", [png]);
    useProfilesMock.mockReturnValue({
      data: profiles([textProfile, visionProfile]),
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useLlmModelProfiles>);
    render(<ComposerVisionHint />);
    expect(screen.queryByTestId("composer-vision-hint")).toBeNull();
  });
});
