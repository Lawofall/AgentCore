// @vitest-environment jsdom
import { Input } from "@/components/ui";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SettingField, SettingsFormMessage } from "../SettingField";

describe("SettingField", () => {
  it("stretches the control by default — the missing w-full bug", () => {
    const { container } = render(
      <SettingField label="Personal Access Token" htmlFor="git-pat">
        <Input id="git-pat" />
      </SettingField>,
    );
    const controlWrap = container.firstElementChild?.children[1] as HTMLElement;
    expect(controlWrap.className).toContain("[&>*]:w-full");
  });

  it("can opt out for controls that keep their intrinsic size", () => {
    const { container } = render(
      <SettingField label="宽度" fullWidth={false}>
        <input aria-label="宽度" />
      </SettingField>,
    );
    const controlWrap = container.firstElementChild?.children[1] as HTMLElement;
    expect(controlWrap.className).not.toContain("[&>*]:w-full");
  });

  it("associates the label with the control", () => {
    render(
      <SettingField label="用户名（可选）" htmlFor="git-username">
        <Input id="git-username" />
      </SettingField>,
    );
    expect(screen.getByLabelText("用户名（可选）").id).toBe("git-username");
  });

  it("exposes aria ids so custom controls can be labelled and described", () => {
    render(
      <SettingField label="主模型" htmlFor="profile-main" hint="必填">
        <button
          type="button"
          id="profile-main"
          aria-labelledby="profile-main-label"
          aria-describedby="profile-main-hint"
        >
          选择模型
        </button>
      </SettingField>,
    );
    expect(document.getElementById("profile-main-label")?.textContent).toBe(
      "主模型",
    );
    expect(document.getElementById("profile-main-hint")?.textContent).toBe(
      "必填",
    );
  });

  it("places the hint below the control or inline with the label", () => {
    const { container, rerender } = render(
      <SettingField label="主模型" hint="必填">
        <input aria-label="主模型" />
      </SettingField>,
    );
    expect(container.querySelector("p")?.textContent).toBe("必填");

    rerender(
      <SettingField label="主模型" hint="必填" hintPlacement="label">
        <input aria-label="主模型" />
      </SettingField>,
    );
    expect(container.querySelector("p")).toBeNull();
    expect(screen.getByText("必填").className).toContain(
      "text-muted-foreground",
    );
  });

  it("announces the error and keeps the hint", () => {
    render(
      <SettingField label="PAT" hint="明文加密落库" error="保存失败，请重试">
        <input aria-label="PAT" />
      </SettingField>,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("保存失败，请重试");
    expect(alert.className).toContain("text-muted-foreground");
    expect(screen.getByText("明文加密落库")).toBeTruthy();
  });

  it("renders a label-line action", () => {
    render(
      <SettingField
        label="组队队员"
        action={<button type="button">恢复跟随</button>}
      >
        <input aria-label="组队队员" />
      </SettingField>,
    );
    expect(screen.getByRole("button", { name: "恢复跟随" })).toBeTruthy();
  });

  it("puts an inline action beside the control, not in the label column", () => {
    const { container } = render(
      <SettingField
        label="邮箱"
        htmlFor="account-email"
        layout="inline"
        action={<span>未验证</span>}
      >
        <Input id="account-email" />
      </SettingField>,
    );
    const row = container.firstElementChild?.firstElementChild as HTMLElement;
    const labelCol = row.firstElementChild as HTMLElement;
    const controlCol = row.lastElementChild as HTMLElement;
    expect(labelCol.textContent).toBe("邮箱");
    expect(controlCol.textContent).toContain("未验证");
  });

  it("puts the label beside the control on inline, and indents the hint", () => {
    const { container } = render(
      <SettingField
        label="名称"
        htmlFor="provider-label"
        layout="inline"
        hint="选择后将预填名称与端点"
      >
        <Input id="provider-label" />
      </SettingField>,
    );
    const row = container.firstElementChild?.firstElementChild as HTMLElement;
    expect(row.className).toContain("md:flex-row");
    expect(row.className).toContain("md:items-start");
    const labelCol = row.firstElementChild as HTMLElement;
    expect(labelCol.className).toContain("md:w-24");
    const hint = screen.getByText("选择后将预填名称与端点");
    expect(hint.className).toContain("md:pl-24");
    expect(screen.getByLabelText("名称").id).toBe("provider-label");
  });

  it("keeps stack as the default — no inline row classes", () => {
    const { container } = render(
      <SettingField label="Token" htmlFor="git-pat">
        <Input id="git-pat" />
      </SettingField>,
    );
    expect(container.firstElementChild?.innerHTML).not.toContain("md:flex-row");
  });
});

describe("SettingsFormMessage", () => {
  it("announces a failure assertively", () => {
    render(<SettingsFormMessage>保存失败，请重试</SettingsFormMessage>);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("保存失败，请重试");
    expect(alert.className).toContain("text-muted-foreground");
  });

  it("announces success politely", () => {
    render(<SettingsFormMessage tone="success">提交成功</SettingsFormMessage>);
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("提交成功");
    expect(status.className).toContain("text-success");
  });

  it("renders nothing without a message, so callers can pass state through", () => {
    const { container } = render(
      <SettingsFormMessage>{null}</SettingsFormMessage>,
    );
    expect(container.firstElementChild).toBeNull();
  });
});
