import type { LlmModelProfileView } from "@/services/llmModelProfiles";
import type { ModelCatalogItem } from "@/services/models";
import { describe, expect, it } from "vitest";
import {
  draftHasImageAttachment,
  lookupComposerProfile,
  profileCanSeeImages,
  resolveComposerProfileId,
  shouldShowComposerVisionHint,
} from "../composerModelProfile";

const textMain: LlmModelProfileView = {
  id: "sys-52",
  name: "Flash",
  kind: "system",
  is_default: true,
  main: { origin: "byok", provider_id: "p1", model: "deepseek-v4-flash" },
  worker: null,
  background: null,
  vision: null,
};

const visionMain: LlmModelProfileView = {
  ...textMain,
  id: "user-vision",
  name: "看图",
  kind: "user",
  is_default: false,
  main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
};

const slotted: LlmModelProfileView = {
  ...textMain,
  id: "user-slot",
  name: "识图槽",
  kind: "user",
  is_default: false,
  vision: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
};

const catalog: ModelCatalogItem[] = [
  {
    id: "deepseek-v4-flash",
    origin: "byok",
    display_name: "DeepSeek V4 Flash",
    vendor: "DeepSeek",
    provider_id: "p1",
    ref: "@byok/p1/deepseek-v4-flash",
    capabilities: [],
    available: true,
  },
  {
    id: "gpt-4o",
    origin: "byok",
    display_name: "GPT-4o",
    vendor: "OpenAI",
    provider_id: "p2",
    ref: "@byok/p2/gpt-4o",
    capabilities: ["vision"],
    available: true,
  },
];

describe("resolveComposerProfileId", () => {
  const ids = ["sys-52", "user-vision", "user-slot"];

  it("prefers the conversation pin", () => {
    expect(
      resolveComposerProfileId({
        conversationId: "c1",
        conversationProfileId: "user-vision",
        draftProfileId: "user-slot",
        lastUsedProfileId: "sys-52",
        profileIds: ids,
      }),
    ).toBe("user-vision");
  });

  it("new chat uses draft pick then valid last-used", () => {
    expect(
      resolveComposerProfileId({
        conversationId: null,
        conversationProfileId: null,
        draftProfileId: "user-slot",
        lastUsedProfileId: "sys-52",
        profileIds: ids,
      }),
    ).toBe("user-slot");
    expect(
      resolveComposerProfileId({
        conversationId: null,
        conversationProfileId: null,
        draftProfileId: null,
        lastUsedProfileId: "user-vision",
        profileIds: ids,
      }),
    ).toBe("user-vision");
  });

  it("ignores last-used that is no longer in the list", () => {
    expect(
      resolveComposerProfileId({
        conversationId: null,
        conversationProfileId: null,
        draftProfileId: null,
        lastUsedProfileId: "gone",
        profileIds: ids,
      }),
    ).toBeNull();
  });

  it("existing chat with no pin does not inherit last-used", () => {
    expect(
      resolveComposerProfileId({
        conversationId: "c1",
        conversationProfileId: null,
        draftProfileId: null,
        lastUsedProfileId: "user-vision",
        profileIds: ids,
      }),
    ).toBeNull();
  });
});

describe("lookupComposerProfile", () => {
  const profiles = [textMain, visionMain];

  it("falls back to account default when the id is missing", () => {
    expect(lookupComposerProfile("missing", profiles, textMain)?.id).toBe(
      "sys-52",
    );
    expect(lookupComposerProfile(null, profiles, textMain)?.id).toBe("sys-52");
  });
});

describe("profileCanSeeImages / vision hint", () => {
  it("true when catalog main has vision or the vision slot is filled", () => {
    expect(profileCanSeeImages(visionMain, catalog)).toBe(true);
    expect(profileCanSeeImages(slotted, catalog)).toBe(true);
    expect(profileCanSeeImages(textMain, catalog)).toBe(false);
  });

  it("hint only when the draft has an image and the combo cannot see", () => {
    expect(
      shouldShowComposerVisionHint({
        hasImage: true,
        profile: textMain,
        catalogModels: catalog,
      }),
    ).toBe(true);
    expect(
      shouldShowComposerVisionHint({
        hasImage: true,
        profile: visionMain,
        catalogModels: catalog,
      }),
    ).toBe(false);
    expect(
      shouldShowComposerVisionHint({
        hasImage: true,
        profile: slotted,
        catalogModels: catalog,
      }),
    ).toBe(false);
    expect(
      shouldShowComposerVisionHint({
        hasImage: false,
        profile: textMain,
        catalogModels: catalog,
      }),
    ).toBe(false);
    expect(
      shouldShowComposerVisionHint({
        hasImage: true,
        profile: visionMain,
        catalogModels: [],
      }),
    ).toBe(false);
    expect(
      shouldShowComposerVisionHint({
        hasImage: true,
        profile: undefined,
        catalogModels: catalog,
      }),
    ).toBe(false);
  });

  it("detects raster filenames, not pdf", () => {
    expect(draftHasImageAttachment([{ name: "shot.png" }])).toBe(true);
    expect(draftHasImageAttachment([{ name: "notes.pdf" }])).toBe(false);
    expect(draftHasImageAttachment([])).toBe(false);
  });
});
