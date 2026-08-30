import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { IntelEfficacy } from "@/lib/intelTypes";
import IntelEfficacyBlock from "./IntelEfficacyBlock";

function efficacy(over: Partial<IntelEfficacy> = {}): IntelEfficacy {
  return {
    enabled: true,
    kMin: 8,
    sampleN: 12,
    overall: { settled: 4, wins: 3, netPnlUsd: 41.2, confirmed: 6, refuted: 2,
      unresolved: 0, samples: 12, accuracy: 0.75 },
    byCategory: [
      { category: "risk", settled: 3, wins: 3, netPnlUsd: 51.2, confirmed: 5, refuted: 0,
        unresolved: 0, samples: 8, accuracy: 1, weight: 0.18 },
      { category: "macro", settled: 1, wins: 0, netPnlUsd: -10, confirmed: 1, refuted: 2,
        unresolved: 0, samples: 4, accuracy: 0.333, weight: 0 },
    ],
    ...over,
  };
}

afterEach(cleanup);

describe("IntelEfficacyBlock", () => {
  it("按类别列出准确率、权重与关联净额", () => {
    render(<IntelEfficacyBlock efficacy={efficacy()} />);
    const rows = screen.getAllByTestId("intel-efficacy-row");
    expect(rows).toHaveLength(2);
    // 文本跨了几个 span（类别名 / 准确率 / 权重各一段），故按整行的 textContent 断言。
    expect(rows[0].textContent).toContain("风险");   // CATEGORY_LABEL.risk
    expect(rows[0].textContent).toContain("准确率 100%");
    expect(rows[0].textContent).toContain("权重 +0.180（排序更靠前）");
    expect(rows[0].textContent).toContain("+51.20 USDT");
  });

  it("样本不足时如实写「样本 3/8，先验未生效」，不画一个假的权重条", () => {
    render(<IntelEfficacyBlock efficacy={efficacy({ sampleN: 3 })} />);
    expect(screen.getByTestId("intel-efficacy-ready")).toHaveTextContent("样本 3/8");
    expect(screen.getByTestId("intel-efficacy-ready")).toHaveTextContent("未生效");
  });

  it("总闸关掉 ⇒ 说「已在设置中关闭」而不是显示一排 0", () => {
    render(<IntelEfficacyBlock efficacy={efficacy({ enabled: false })} />);
    expect(screen.getByTestId("intel-efficacy-ready")).toHaveTextContent("已在设置中关闭");
    expect(screen.queryAllByTestId("intel-efficacy-row")).toHaveLength(0);
  });

  it("还没有任何样本时给一句解释，而不是一张空表", () => {
    render(<IntelEfficacyBlock efficacy={efficacy({ byCategory: [], sampleN: 0 })} />);
    expect(screen.getByText(/还没有任何已结算的引用或核查判定/)).toBeInTheDocument();
  });

  it("老后端 / 未装配 ⇒ 整块不渲染（绝不显示一个凭空的 0%）", () => {
    const { container } = render(<IntelEfficacyBlock efficacy={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("准确率为 null 的行写「样本不足」", () => {
    render(<IntelEfficacyBlock efficacy={efficacy({
      byCategory: [{ category: "onchain", settled: 0, wins: 0, netPnlUsd: 0, confirmed: 1,
        refuted: 0, unresolved: 0, samples: 1, accuracy: null, weight: 0 }] })} />);
    expect(screen.getByText(/准确率 样本不足/)).toBeInTheDocument();
  });

  it("标题里明说它只影响排序与举证责任，不是门禁（R-6）", () => {
    render(<IntelEfficacyBlock efficacy={efficacy()} />);
    expect(screen.getByText(/不改变任何一条情报能否入库或被注入/)).toBeInTheDocument();
  });
});
