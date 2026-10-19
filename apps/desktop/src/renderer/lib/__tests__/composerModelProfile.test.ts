import type { ModelCatalogItem } from "@/services/models";

type ProfileRow = {
  id: string;
  name: string;
  kind: "system" | "user";
  is_default: boolean;
  main: { origin: "byok"; provider_id: string; model: string };
  worker: null;
  reasoning_effort?: string | null;
  context_budget?: number | null;
};
import { describe, expect, it } from "vitest";
import {
  byokVisionDeclareRef,
  catalogComposerKnobs,
  completeModelOverride,
  draftHasImageAttachment,
  lookupComposerProfile,
  overlayProfileModel,
  profileCanSeeImages,
  resolveComposerOverride,
  resolveComposerProfileId,
  restoreComposerKnobs,
  shouldOfferComposerVisionDeclare,
  shouldShowComposerVisionHint,
} from "../composerModelProfile";

const textMain: ProfileRow = {
  id: "sys-52",
  name: "Flash",
  kind: "system",
  is_default: true,
  main: { origin: "byok", provider_id: "p1", model: "deepseek-v4-flash" },
  worker: null,
};

const visionMain: ProfileRow = {
  ...textMain,
  id: "user-vision",
  name: "看图",
  kind: "user",
  is_default: false,
  main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
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
    custom: false,
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
    custom: false,
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
  it("true when catalog main has vision", () => {
    expect(profileCanSeeImages(visionMain, catalog)).toBe(true);
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

  it("BYOK unknown main can declare; platform / seeing main cannot", () => {
    expect(byokVisionDeclareRef(textMain)).toBe("@byok/p1/deepseek-v4-flash");
    expect(
      shouldOfferComposerVisionDeclare({
        hasImage: true,
        profile: textMain,
        catalogModels: catalog,
      }),
    ).toBe(true);
    expect(
      shouldOfferComposerVisionDeclare({
        hasImage: true,
        profile: visionMain,
        catalogModels: catalog,
      }),
    ).toBe(false);
    expect(
      byokVisionDeclareRef({
        main: { origin: "platform", model: "glm-5.3" },
      }),
    ).toBeNull();
  });

  it("detects raster filenames, not pdf", () => {
    expect(draftHasImageAttachment([{ name: "shot.png" }])).toBe(true);
    expect(draftHasImageAttachment([{ name: "notes.pdf" }])).toBe(false);
    expect(draftHasImageAttachment([])).toBe(false);
  });
});

describe("overlayProfileModel", () => {
  it("keeps the assembly when there is no override", () => {
    expect(overlayProfileModel(textMain, null)?.main.model).toBe(
      "deepseek-v4-flash",
    );
  });

  it("writes this conversation's main over the assembly", () => {
    const next = overlayProfileModel(textMain, {
      main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
    });
    expect(next?.main.model).toBe("gpt-4o");
    expect(next?.id).toBe("sys-52");
  });

  it("does not inherit assembly effort when the snapshot leaves it null", () => {
    const base = { ...textMain, reasoning_effort: "low" as const };
    const next = overlayProfileModel(base, {
      main: { origin: "byok", provider_id: "p1", model: "deepseek-v4-flash" },
    });
    expect(next?.reasoning_effort).toBeNull();
    expect(next?.worker).toBeNull();
    expect(next?.context_budget).toBeNull();
  });
});

describe("resolveComposerOverride", () => {
  const snap = completeModelOverride({
    main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
    reasoning_effort: "low",
  });

  it("existing chat uses the conversation pin, not the account last-used", () => {
    expect(
      resolveComposerOverride({
        conversationId: "c1",
        conversationOverride: snap,
        draftOverride: null,
        accountComposer: completeModelOverride({
          main: {
            origin: "byok",
            provider_id: "p1",
            model: "deepseek-v4-flash",
          },
        }),
      }),
    ).toEqual(snap);
    expect(
      resolveComposerOverride({
        conversationId: "c1",
        conversationOverride: null,
        draftOverride: snap,
        accountComposer: snap,
      }),
    ).toBeNull();
  });

  it("new chat uses draft then account last-used", () => {
    expect(
      resolveComposerOverride({
        conversationId: null,
        conversationOverride: null,
        draftOverride: snap,
        accountComposer: null,
      }),
    ).toEqual(snap);
    expect(
      resolveComposerOverride({
        conversationId: null,
        conversationOverride: null,
        draftOverride: null,
        accountComposer: snap,
      }),
    ).toEqual(snap);
  });
});

describe("completeModelOverride", () => {
  it("fills every knob so omitted keys cannot inherit assembly", () => {
    expect(
      completeModelOverride({
        main: { origin: "platform", provider_id: null, model: "glm-5.2" },
      }),
    ).toEqual({
      main: { origin: "platform", provider_id: null, model: "glm-5.2" },
      worker: null,
      reasoning_effort: null,
      context_budget: null,
    });
  });
});

describe("restoreComposerKnobs", () => {
  const effortCatalog: ModelCatalogItem[] = [
    {
      ...catalog[0],
      id: "deepseek-v4-pro",
      ref: "@byok/p1/deepseek-v4-pro",
      reasoning_effort: { options: ["low", "high", "max"], default: "high" },
      context_length: 1_000_000,
    },
    catalog[1],
  ];

  it("restores that leaf's last knobs", () => {
    expect(
      restoreComposerKnobs(
        { origin: "byok", provider_id: "p1", model: "deepseek-v4-pro" },
        {
          "@byok/p1/deepseek-v4-pro": {
            reasoning_effort: "low",
            context_budget: 128_000,
          },
        },
        effortCatalog,
      ),
    ).toEqual({ reasoning_effort: "low", context_budget: 128_000 });
  });

  it("unused leaf is vendor default, not the previous model's knobs", () => {
    expect(
      restoreComposerKnobs(
        { origin: "byok", provider_id: "p1", model: "deepseek-v4-pro" },
        {
          "@byok/p2/gpt-4o": {
            reasoning_effort: "low",
            context_budget: 128_000,
          },
        },
        effortCatalog,
      ),
    ).toEqual({ reasoning_effort: null, context_budget: null });
  });

  it("seeds the current snapshot when the map has not stored that leaf yet", () => {
    expect(
      catalogComposerKnobs({
        composer_model: {
          main: {
            origin: "byok",
            provider_id: "p1",
            model: "deepseek-v4-flash",
          },
          reasoning_effort: "low",
          context_budget: null,
        },
      } as never),
    ).toEqual({
      "@byok/p1/deepseek-v4-flash": {
        reasoning_effort: "low",
        context_budget: null,
      },
    });
  });
});
