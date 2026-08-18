// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ BASE_URL: "https://api.example" }));

import { ImAvatar } from "@/pages/im/ImAvatar";

afterEach(cleanup);

describe("ImAvatar", () => {
  it("renders an image for a server url and prefixes relative paths", () => {
    const { container } = render(
      <ImAvatar name="Alice" url="/avatars/a.png" />,
    );
    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "https://api.example/avatars/a.png",
    );
  });

  it("renders the name initial when url is null", () => {
    const { container } = render(<ImAvatar name="Alice" url={null} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("span")?.textContent).toBe("A");
  });

  it("falls back to the initial when the image errors", () => {
    const { container } = render(
      <ImAvatar name="Alice" url="/avatars/a.png" />,
    );
    const img = container.querySelector("img");
    if (!img)
      throw new Error("expected an <img> before firing its error event");
    fireEvent.error(img);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("span")?.textContent).toBe("A");
  });
});
