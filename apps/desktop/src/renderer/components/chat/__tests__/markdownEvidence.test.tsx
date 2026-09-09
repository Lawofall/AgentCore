// @vitest-environment jsdom

import { EvidenceLedgerProvider } from "@/components/chat/EvidenceLedgerContext";
import { Markdown } from "@/components/chat/Markdown";
import { buildLedgerMap } from "@/lib/evidenceLedger";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

/**
 * 10 P3-4: end-to-end guard for the evidence-badge seam (举证责任 P3 + 证据台账 M1).
 * The chip only appears if a whole chain holds: remarkEvidence rewrites markers into an
 * `evidencemark` node → react-markdown maps that custom element to {@link EvidenceBadge}
 * → the node's `data.hProperties.dataKind` survives as `data-kind`. Ledger-resolved `#eN`
 * badges become clickable; unresolved / legacy free-text stay plain.
 */
describe("Markdown evidence badges (render seam)", () => {
  it("renders 【已核实·出处】 as a verified badge carrying the source", () => {
    render(<Markdown content="降本【已核实·2024报表】约 18%" evidence />);
    const verified = screen.getByTitle(/有据可查/);
    expect(verified.textContent).toContain("已核实");
    expect(verified.textContent).toContain("2024报表");
    expect(screen.queryByTitle(/暂无出处/)).toBeNull();
  });

  it("renders a bare 【待核实】 as the unverified badge", () => {
    render(<Markdown content="这条【待核实】仍存疑" evidence />);
    const unverified = screen.getByTitle(/暂无出处/);
    expect(unverified.textContent).toContain("待核实");
    expect(screen.queryByTitle(/有据可查/)).toBeNull();
  });

  it("renders both kinds distinctly in one line", () => {
    render(<Markdown content="A【已核实·甲】B【待核实·推断】C" evidence />);
    expect(screen.getByTitle(/有据可查/).textContent).toContain("甲");
    expect(screen.getByTitle(/暂无出处/).textContent).toContain("推断");
  });

  it("leaves the marker literal when evidence is off (no badge)", () => {
    render(<Markdown content="降本【已核实·2024报表】约 18%" />);
    expect(screen.queryByTitle(/有据可查/)).toBeNull();
    expect(screen.getByText(/【已核实·2024报表】/)).toBeTruthy();
  });

  it("resolves #eN badge label from ledger context", () => {
    const ledger = buildLedgerMap([
      {
        id: "#e3",
        url: "https://court.gov.cn/x",
        title: "判决书",
        site: "court.gov.cn",
        date: "2024-01-01",
        tier: "official",
        side_key: "pro",
      },
    ]);
    render(
      <EvidenceLedgerProvider ledger={ledger}>
        <Markdown content="降本【已核实·#e3】约 18%" evidence />
      </EvidenceLedgerProvider>,
    );
    const verified = screen.getByRole("button", {
      name: /已核实 · court.gov.cn/,
    });
    expect(verified.textContent).toContain("court.gov.cn");
    expect(verified.textContent).not.toContain("#e3");
    expect(verified.textContent).not.toContain("官方");
    expect(verified.textContent).not.toContain("弱源");
    expect(verified.textContent).not.toContain("待评");
  });

  it("falls back to plain text badge when #eN is unresolved", () => {
    render(<Markdown content="降本【已核实·#e99】约 18%" evidence />);
    const verified = screen.getByTitle(/有据可查/);
    expect(verified.textContent).toContain("#e99");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("leaves legacy free-text markers as non-interactive badges", () => {
    render(<Markdown content="降本【已核实·2024报表】约 18%" evidence />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByTitle(/有据可查/).textContent).toContain("2024报表");
  });

  it("shows dossier source in ledger popover when dossier_path is set", () => {
    const ledger = buildLedgerMap([
      {
        id: "#e2",
        url: "https://court.example/x",
        title: "法律 · #r1",
        site: "法律",
        tier: "unknown",
        side_key: "dossier",
        dossier_path: "AgentCore/文档/research/法律透镜报告.md",
        origin_id: "#r1",
        dossier_label: "法律",
      },
    ]);
    render(
      <EvidenceLedgerProvider ledger={ledger}>
        <Markdown content="条款【已核实·#e2】成立" evidence />
      </EvidenceLedgerProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /已核实 · 法律/ }));
    expect(screen.queryByText("来源待评")).toBeNull();
    expect(screen.queryByText("待评")).toBeNull();
    expect(screen.getByText(/约定文档来源/)).toBeTruthy();
    expect(screen.getByText(/法律透镜报告\.md/)).toBeTruthy();
    expect(screen.getByText(/幕1 出处 #r1/)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /打开约定文档文件/ }),
    ).toBeTruthy();
  });
});
