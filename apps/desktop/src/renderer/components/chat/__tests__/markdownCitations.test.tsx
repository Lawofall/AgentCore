// @vitest-environment jsdom

import { Markdown } from "@/components/chat/Markdown";
import { SourceCards } from "@/components/chat/SourceCards";
import { TooltipProvider } from "@/components/ui/tooltip";
import { buildCitationDisplayMap } from "@/lib/citationDisplayMap";
import type { Citation } from "@/types/events";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

const CITATIONS: Citation[] = [
  {
    url: "https://a.example/one",
    title: "Source A",
    snippet: "snip A",
    site: "a.example",
    tier: "unknown",
  },
  {
    url: "https://b.example/two",
    title: "Source B",
    snippet: "snip B",
    site: "b.example",
    tier: "media",
  },
  {
    url: "https://c.example/three",
    title: "Source C",
    snippet: "snip C",
    site: "c.example",
    tier: "official",
  },
];

function renderWithTooltip(ui: ReactNode) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

describe("Markdown citation chips (render seam)", () => {
  it("renders in-range [n] as chips linking to the real source URL", () => {
    const display = buildCitationDisplayMap("see [2] and [1]", 3);
    renderWithTooltip(
      <Markdown
        content="see [2] and [1]"
        citations={CITATIONS}
        citationToDisplay={display.toDisplay}
      />,
    );
    const chip2 = screen.getByRole("link", { name: "来源 1" });
    const chip1 = screen.getByRole("link", { name: "来源 2" });
    expect(chip2.getAttribute("href")).toBe("https://b.example/two");
    expect(chip1.getAttribute("href")).toBe("https://a.example/one");
    expect(chip2.getAttribute("target")).toBe("_blank");
    expect(chip2.getAttribute("rel")).toBe("noreferrer");
    expect(chip2.textContent).toBe("1");
    expect(chip1.textContent).toBe("2");
    expect(chip2.querySelector("img")?.getAttribute("src")).toContain(
      "b.example",
    );
    expect(chip2.className).toContain("bg-muted");
    expect(chip2.className).not.toContain("bg-primary");
  });

  it("falls back to a site letter when the favicon fails to load", () => {
    const display = buildCitationDisplayMap("see [1]", 1);
    renderWithTooltip(
      <Markdown
        content="see [1]"
        citations={CITATIONS.slice(0, 1)}
        citationToDisplay={display.toDisplay}
      />,
    );
    const chip = screen.getByRole("link", { name: "来源 1" });
    const img = chip.querySelector("img");
    expect(img).toBeInstanceOf(HTMLImageElement);
    if (!(img instanceof HTMLImageElement)) return;
    fireEvent.error(img);
    expect(chip.querySelector("img")).toBeNull();
    expect(chip.textContent).toContain("A");
    expect(chip.textContent).toContain("1");
  });

  it("renders [1]-[2] as two chips without a visible hyphen", () => {
    const display = buildCitationDisplayMap("见 [1]-[2]", 2);
    const { container } = renderWithTooltip(
      <Markdown
        content="见 [1]-[2]"
        citations={CITATIONS.slice(0, 2)}
        citationToDisplay={display.toDisplay}
      />,
    );
    expect(screen.getByRole("link", { name: "来源 1" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "来源 2" })).toBeTruthy();
    expect(container.textContent).not.toMatch(/1\s*-\s*2/);
  });

  it("leaves out-of-range markers as plain text (no chip link)", () => {
    const display = buildCitationDisplayMap("ok [1] bad [9]", 2);
    renderWithTooltip(
      <Markdown
        content="ok [1] bad [9]"
        citations={CITATIONS.slice(0, 2)}
        citationToDisplay={display.toDisplay}
      />,
    );
    expect(
      screen.getByRole("link", { name: "来源 1" }).getAttribute("href"),
    ).toBe("https://a.example/one");
    expect(screen.queryByRole("link", { name: /来源 9/ })).toBeNull();
    expect(screen.getByText(/\[9\]/)).toBeTruthy();
  });

  it("does not invent chips when citations are absent", () => {
    renderWithTooltip(<Markdown content="see [1]" />);
    expect(screen.queryByRole("link", { name: /来源/ })).toBeNull();
    expect(screen.getByText(/\[1\]/)).toBeTruthy();
  });

  it("renders consecutive #rN ledger chips when knownLedgerIds + citation.id match", () => {
    const ledgerCites: Citation[] = [
      {
        url: "https://a.example/one",
        title: "Source A",
        snippet: "snip A",
        site: "a.example",
        id: "#r5",
        tier: "media",
      },
      {
        url: "https://b.example/two",
        title: "Source B",
        snippet: "snip B",
        site: "b.example",
        id: "#r3",
        tier: "unknown",
      },
      {
        url: "https://c.example/three",
        title: "Source C",
        snippet: "snip C",
        site: "c.example",
        id: "#r11",
        tier: "unknown",
      },
    ];
    const known = new Set(["#r5", "#r3", "#r11"]);
    renderWithTooltip(
      <Markdown
        content="争议 **粗体** #r5#r3#r11"
        citations={ledgerCites}
        knownLedgerIds={known}
      />,
    );
    expect(screen.queryByText(/#r5#r3#r11/)).toBeNull();
    expect(
      screen.getByRole("link", { name: /来源 .*（#r5）/ }).getAttribute("href"),
    ).toBe("https://a.example/one");
    expect(
      screen.getByRole("link", { name: /来源 .*（#r3）/ }).getAttribute("href"),
    ).toBe("https://b.example/two");
    expect(
      screen
        .getByRole("link", { name: /来源 .*（#r11）/ })
        .getAttribute("href"),
    ).toBe("https://c.example/three");
  });

  it("renders #rN from evidenceLedger when citations[].id is missing (timing fallback)", () => {
    renderWithTooltip(
      <Markdown
        content="见 #r5"
        citations={CITATIONS}
        evidenceLedger={[
          {
            id: "#r5",
            url: "https://ledger.example/r5",
            title: "Ledger R5",
            site: "ledger.example",
            tier: "media",
          },
        ]}
      />,
    );
    expect(screen.queryByText(/#r5/)).toBeNull();
    expect(
      screen.getByRole("link", { name: /来源 .*（#r5）/ }).getAttribute("href"),
    ).toBe("https://ledger.example/r5");
  });

  it("renders #rN after a GFM table + bold (debate brief shape)", () => {
    const ledgerCites: Citation[] = [
      {
        url: "https://a.example/5",
        title: "R5",
        snippet: "",
        site: "a.example",
        id: "#r5",
      },
      {
        url: "https://b.example/3",
        title: "R3",
        snippet: "",
        site: "b.example",
        id: "#r3",
      },
      {
        url: "https://c.example/11",
        title: "R11",
        snippet: "",
        site: "c.example",
        id: "#r11",
      },
    ];
    const content = [
      "| 要素 | 内容 |",
      "|---|---|",
      "| **当事人** | 原告 |",
      "",
      "核心法律争议：**四瓣花显著性？** #r5#r3#r11",
    ].join("\n");
    renderWithTooltip(
      <Markdown
        content={content}
        citations={ledgerCites}
        knownLedgerIds={new Set(["#r5", "#r3", "#r11"])}
      />,
    );
    expect(screen.queryByText(/#r5#r3#r11/)).toBeNull();
    expect(screen.getByRole("link", { name: /#r5/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /#r3/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /#r11/ })).toBeTruthy();
  });
});

describe("SourceCards display numbers", () => {
  it("shows display numbers from the shared map and orders cited first", () => {
    const display = buildCitationDisplayMap("cite [3] then [1]", 3);
    renderWithTooltip(
      <SourceCards citations={CITATIONS} displayMap={display} />,
    );
    // Collapsed pills: display 1 = pool[2], display 2 = pool[0], display 3 = pool[1]
    const links = screen.getAllByRole("link");
    expect(links[0].getAttribute("href")).toBe("https://c.example/three");
    expect(links[0].textContent).toMatch(/^1/);
    expect(links[1].getAttribute("href")).toBe("https://a.example/one");
    expect(links[1].textContent).toMatch(/^2/);
    expect(links[2].getAttribute("href")).toBe("https://b.example/two");
    expect(links[2].textContent).toMatch(/^3/);
  });

  it("does not render domain-tier badges on source pills", () => {
    renderWithTooltip(<SourceCards citations={CITATIONS} />);
    expect(screen.queryByText("官方")).toBeNull();
    expect(screen.queryByText("媒体")).toBeNull();
    expect(screen.queryByText("待评")).toBeNull();
    expect(screen.queryByText("弱源")).toBeNull();
    expect(screen.queryByText("已读")).toBeNull();
  });

  it("marks 已读 when the citation was fetched", () => {
    renderWithTooltip(
      <SourceCards
        citations={[{ ...CITATIONS[0], deep_read: true, id: "#r1" }]}
      />,
    );
    expect(screen.getByText("已读")).toBeTruthy();
    expect(screen.queryByText("待评")).toBeNull();
  });

  it("marks 已读 from the evidence ledger when the citation omits deep_read", () => {
    renderWithTooltip(
      <SourceCards
        citations={[{ ...CITATIONS[0], id: "#r1" }]}
        evidenceLedger={[
          {
            id: "#r1",
            url: "https://a.example/one",
            title: "Source A",
            site: "a.example",
            deep_read: true,
          },
        ]}
      />,
    );
    expect(screen.getByText("已读")).toBeTruthy();
  });

  it("omits tier labels from the source ledger popover", () => {
    const cites: Citation[] = [
      { ...CITATIONS[0], id: "#r1", tier: "unknown" },
      { ...CITATIONS[1], id: "#r2" },
      { ...CITATIONS[2], id: "#r3" },
      {
        url: "https://d.example/four",
        title: "Source D",
        snippet: "snip D",
        site: "d.example",
        id: "#r4",
        tier: "weak",
      },
    ];
    renderWithTooltip(<SourceCards citations={cites} />);
    fireEvent.click(screen.getByRole("button", { name: /来源 4/ }));
    fireEvent.click(screen.getByRole("button", { name: "查看来源台账 #r1" }));
    expect(screen.queryByText("来源待评")).toBeNull();
    expect(screen.queryByText("自媒体")).toBeNull();
    expect(screen.queryByText("弱源")).toBeNull();
    expect(screen.queryByText("官方")).toBeNull();
  });
});
