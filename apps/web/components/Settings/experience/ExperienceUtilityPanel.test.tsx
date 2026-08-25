import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExperienceUtilityPanel from "./ExperienceUtilityPanel";
import type { ExperienceUtilityPage } from "@/lib/experienceTypes";

// 效用面板（ai-trade-experience-compounding W-C / W-F）。
//
// 这一屏是用户**据以删掉一条经验**的地方，所以它的措辞就是它的正确性：
// 「注入后表现」「与事实一致率」，绝不「贡献」「带来」——那会把一个共现关系说成因果（J-5）。

const PAGE: ExperienceUtilityPage = {
  items: [
    { key: "lesson:bad", id: 1, label: "BTC 15m · 方向·做多：会亏。", muted: true,
      muteReason: "ineffective", pinned: false, injected: 21, scored: 21,
      meanNetAfter: 18.4, tAfter: 2.6, agreement: 0.29, claimSign: -1, ineffective: true },
    { key: "lesson:ok", id: 2, label: "ETH 1H · 方向·做空：会赚。", muted: false,
      muteReason: null, pinned: false, injected: 9, scored: 9,
      meanNetAfter: 12.0, tAfter: 1.1, agreement: 0.66, claimSign: 1, ineffective: false },
    { key: "lesson:pin", id: 3, label: "置顶的一条。", muted: false, muteReason: null,
      pinned: true, injected: 30, scored: 30, meanNetAfter: -18.4, tAfter: -2.9,
      agreement: 0.31, claimSign: 1, ineffective: true },
  ],
  total: 3, asOfTs: 1_700_000_000, minSamples: 12, tThreshold: 2,
};

afterEach(cleanup);

describe("ExperienceUtilityPanel", () => {
  it("没有任何注入样本 ⇒ 一句说明，而不是一张空表", () => {
    render(<ExperienceUtilityPanel page={{ ...PAGE, items: [], total: 0 }} loading={false}
                                   onOpen={vi.fn()} onRestore={vi.fn()} />);
    expect(screen.getByTestId("experience-utility-empty"))
      .toHaveTextContent("还没有任何一条经验被喂进过");
  });

  it("把失效判据的三重门槛逐字说给用户听（I-8：算了就要有渲染落点）", () => {
    render(<ExperienceUtilityPanel page={PAGE} loading={false}
                                   onOpen={vi.fn()} onRestore={vi.fn()} />);
    const panel = screen.getByTestId("experience-utility-panel");
    expect(panel).toHaveTextContent("注入 12 次以上");
    expect(panel).toHaveTextContent("系统性相反");
    expect(panel).toHaveTextContent("|t| ≥ 2");
  });

  it("J-5 · 这一屏必须点明「共现，不是因果」，且不出现任何因果措辞", () => {
    render(<ExperienceUtilityPanel page={PAGE} loading={false}
                                   onOpen={vi.fn()} onRestore={vi.fn()} />);
    const panel = screen.getByTestId("experience-utility-panel");
    expect(panel).toHaveTextContent("共现关系，不是因果");
    expect(panel.textContent ?? "").not.toMatch(/贡献|带来了|因此赚|导致盈利/);
    expect(panel).toHaveTextContent("与事实一致率");
  });

  it("已自动静音的行给出「恢复启用」，且用**形状**而不只是颜色标出来", () => {
    const onRestore = vi.fn();
    render(<ExperienceUtilityPanel page={PAGE} loading={false}
                                   onOpen={vi.fn()} onRestore={onRestore} />);
    const rows = screen.getAllByTestId("experience-utility-row");
    expect(rows[0]).toHaveTextContent("已自动静音");
    expect(rows[0]).toHaveStyle({ borderLeft: "2px solid var(--warn)" });
    fireEvent.click(screen.getByRole("button", { name: "恢复启用" }));
    expect(onRestore).toHaveBeenCalledWith(PAGE.items[0]);
  });

  it("置顶的经验读数照常展示，但明说它不会被自动静音（用户意志优先于统计）", () => {
    render(<ExperienceUtilityPanel page={PAGE} loading={false}
                                   onOpen={vi.fn()} onRestore={vi.fn()} />);
    const rows = screen.getAllByTestId("experience-utility-row");
    expect(rows[2]).toHaveTextContent("已置顶 · 不自动静音");
    expect(rows[2]).toHaveTextContent("-18.4bps");
  });

  it("点陈述打开详情（面板与列表通向同一个抽屉）", () => {
    const onOpen = vi.fn();
    render(<ExperienceUtilityPanel page={PAGE} loading={false}
                                   onOpen={onOpen} onRestore={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: PAGE.items[1].label }));
    expect(onOpen).toHaveBeenCalledWith(PAGE.items[1]);
  });
});
