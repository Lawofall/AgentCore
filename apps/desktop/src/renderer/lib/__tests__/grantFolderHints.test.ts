import { describe, expect, it } from "vitest";
import {
  ATTACH_CONFIRM_CTA,
  grantHintsFromAskOption,
  isAttachOralConsent,
  isOrganizeOralConsent,
  organizeConfirmDetail,
  pickOralGrantOption,
  previewOrganizeTargetLabel,
} from "../grantFolderHints";

describe("grantHintsFromAskOption", () => {
  it("returns undefined when neither hint is set", () => {
    expect(grantHintsFromAskOption({})).toBeUndefined();
    expect(grantHintsFromAskOption({ well_known: "home" })).toBeUndefined();
  });

  it("maps snake_case wire fields to camelCase IPC hints", () => {
    expect(
      grantHintsFromAskOption({
        well_known: "desktop",
        target_name: "  6月报表  ",
      }),
    ).toEqual({ wellKnown: "desktop", targetName: "6月报表" });
  });

  it("allows wellKnown alone", () => {
    expect(grantHintsFromAskOption({ well_known: "downloads" })).toEqual({
      wellKnown: "downloads",
    });
  });

  it("allows targetName alone", () => {
    expect(grantHintsFromAskOption({ target_name: "Docs" })).toEqual({
      targetName: "Docs",
    });
  });

  it("forwards mount path so preview and fulfill share the same hint", () => {
    expect(
      grantHintsFromAskOption({
        path: "C:\\Users\\me\\Desktop\\咨询",
        well_known: "desktop",
        target_name: "咨询",
      }),
    ).toEqual({
      path: "C:\\Users\\me\\Desktop\\咨询",
      wellKnown: "desktop",
      targetName: "咨询",
    });
    expect(
      grantHintsFromAskOption({ path: "  /home/me/Downloads/pack  " }),
    ).toEqual({ path: "/home/me/Downloads/pack" });
  });
});

describe("previewOrganizeTargetLabel", () => {
  it("synthesizes well_known + target_name as 桌面 › 咨询", () => {
    expect(
      previewOrganizeTargetLabel({
        well_known: "desktop",
        target_name: "咨询",
      }),
    ).toBe("桌面 › 咨询");
  });

  it("uses basename for absolute path (never full abs)", () => {
    expect(
      previewOrganizeTargetLabel({ path: "C:\\Users\\me\\Desktop\\咨询" }),
    ).toBe("咨询");
    expect(
      previewOrganizeTargetLabel({ path: "/home/me/Downloads/pack" }),
    ).toBe("pack");
  });

  it("falls back to well_known or target alone", () => {
    expect(previewOrganizeTargetLabel({ well_known: "documents" })).toBe(
      "文档",
    );
    expect(previewOrganizeTargetLabel({ target_name: "仅子名" })).toBe(
      "仅子名",
    );
  });
});

describe("organizeConfirmDetail", () => {
  it("prefixes 将整理 for grant_organize_folder", () => {
    expect(
      organizeConfirmDetail({
        action: "grant_organize_folder",
        well_known: "desktop",
        target_name: "咨询",
      }),
    ).toBe("将整理：桌面 › 咨询");
  });

  it("does not pass through model detail for non-organize options", () => {
    expect(
      organizeConfirmDetail({
        action: "bind_local_folder",
        detail: "只读说明",
      }),
    ).toBeUndefined();
  });

  it("ignores model detail on organize grant (structured line only)", () => {
    expect(
      organizeConfirmDetail({
        action: "grant_organize_folder",
        well_known: "desktop",
        target_name: "咨询",
        detail: "模型发挥的副标题",
      }),
    ).toBe("将整理：桌面 › 咨询");
  });
});

describe("isOrganizeOralConsent", () => {
  it("hits short allowlist exactly", () => {
    for (const phrase of [
      "可以",
      "允许",
      "同意",
      "好的",
      "可以整理",
      "允许整理",
      "升级可整理",
    ]) {
      expect(isOrganizeOralConsent(phrase)).toBe(true);
    }
    expect(isOrganizeOralConsent("  可以。  ")).toBe(true);
  });

  it("rejects ordinary / long other text (no intent classify)", () => {
    expect(isOrganizeOralConsent("先放一放，下周再说")).toBe(false);
    expect(isOrganizeOralConsent("不可以")).toBe(false);
    expect(isOrganizeOralConsent("可以整理一下桌面上的咨询文件夹")).toBe(false);
    expect(isOrganizeOralConsent("")).toBe(false);
  });
});

describe("isAttachOralConsent", () => {
  it("hits attach-specific phrases only (not 可以/允许)", () => {
    for (const phrase of ["可以改", "允许改", "可以覆盖", ATTACH_CONFIRM_CTA]) {
      expect(isAttachOralConsent(phrase)).toBe(true);
    }
    expect(isAttachOralConsent("可以")).toBe(false);
    expect(isAttachOralConsent("允许")).toBe(false);
    expect(isAttachOralConsent("好的")).toBe(false);
  });
});

describe("pickOralGrantOption", () => {
  const organize = { label: "整理", action: "grant_organize_folder" };
  const attach = { label: "可写", action: "grant_attach_folder" };

  it("generic 可以 on a mixed card picks organize, not attach", () => {
    expect(pickOralGrantOption([attach, organize], "可以")).toEqual(organize);
    expect(pickOralGrantOption([organize, attach], "可以")).toEqual(organize);
  });

  it("attach-specific phrase on a mixed card picks attach", () => {
    expect(pickOralGrantOption([attach, organize], "可以改")).toEqual(attach);
  });

  it("generic 可以 on attach-only does not auto-grant", () => {
    expect(pickOralGrantOption([attach], "可以")).toBeUndefined();
  });

  it("does not pick when two options would both match", () => {
    expect(
      pickOralGrantOption(
        [
          { action: "grant_organize_folder" },
          { action: "grant_organize_folder" },
        ],
        "可以",
      ),
    ).toBeUndefined();
  });
});
