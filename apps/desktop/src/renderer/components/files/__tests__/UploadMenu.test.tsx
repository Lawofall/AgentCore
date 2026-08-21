// @vitest-environment jsdom
import { UploadMenu } from "@/components/files/UploadMenu";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  cleanup();
});

describe("UploadMenu", () => {
  it("one toolbar control; menu splits file vs folder pickers", () => {
    const onUploadFiles = vi.fn();
    const onUploadFolder = vi.fn();
    render(
      <TooltipProvider>
        <UploadMenu
          uploading={false}
          onUploadFiles={onUploadFiles}
          onUploadFolder={onUploadFolder}
        />
      </TooltipProvider>,
    );

    expect(screen.queryByRole("button", { name: "上传文件夹" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "上传" }));
    fireEvent.click(screen.getByRole("button", { name: "上传文件" }));
    expect(onUploadFiles).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "上传" }));
    fireEvent.click(screen.getByRole("button", { name: "上传文件夹" }));
    expect(onUploadFolder).toHaveBeenCalledTimes(1);
  });
});
