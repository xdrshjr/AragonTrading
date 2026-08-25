import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExperienceList from "./ExperienceList";
import type { ExperienceNode } from "@/lib/experienceTypes";

// 列表视图 —— 图谱的**无障碍等价形态**。只有图的界面对读屏用户不可用，而需求写的是
// 「符合人机交互最佳实践」。这批断言守的是「它真的是一张可读的表」，而不是一堆 div。

function node(over: Partial<ExperienceNode> = {}): ExperienceNode {
  return {
    id: 1, kind: "lesson", key: "lesson:a", axis: null, value: null,
    label: "BTC-USDT-SWAP 15m 高波动+突破 做多：23 笔净额均值 −31bps", detail: "",
    source: "stat", scope: "symbol", tier: "iron", pinned: false, muted: false,
    muteReason: null, support: 23, weight: 0.9,
    stats: { meanNetBps: -31.2, tStat: -2.83 },
    signature: { symbol: "BTC-USDT-SWAP", volatility: "high_volatility", side: "long" },
    firstTs: 0, lastSeenTs: 1_700_000_000, signStreak: 2, createdTs: 0, updatedTs: 0,
    ...over,
  };
}

afterEach(cleanup);

function renderList(items: ExperienceNode[], total = items.length) {
  const props = {
    items, total, loading: false,
    onOpen: vi.fn(), onPin: vi.fn(), onMute: vi.fn(), onDelete: vi.fn(),
  };
  render(<ExperienceList {...props} />);
  return props;
}

describe("ExperienceList", () => {
  it("空筛选结果 ⇒ 引导态而不是一张空表", () => {
    renderList([]);
    expect(screen.getByTestId("experience-list-empty"))
      .toHaveTextContent("完成第一次自动交易后");
  });

  it("是一张有表头与 caption 的真表（读屏用户能听懂列的含义）", () => {
    renderList([node()]);
    expect(screen.getByRole("table")).toBeVisible();
    for (const header of ["陈述", "档位", "来源", "样本 n", "净额均值", "t"]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeVisible();
    }
  });

  it("统计量按人话渲染，缺席的显示「—」而不是 0", () => {
    renderList([node({ stats: {} })]);
    const row = screen.getByTestId("experience-list-row");
    expect(row).toHaveTextContent("—");
    cleanup();
    renderList([node()]);
    expect(screen.getByTestId("experience-list-row")).toHaveTextContent("-31.2bps");
    expect(screen.getByTestId("experience-list-row")).toHaveTextContent("-2.8");
  });

  it("`stat` 经验的删除按钮**禁用**并说明理由（删了会被重建复活，是个假动作）", () => {
    renderList([node()]);
    const remove = screen.getByRole("button", { name: "删除" });
    expect(remove).toBeDisabled();
    expect(remove).toHaveAttribute("title", expect.stringContaining("静音"));
  });

  it("手工经验可删；置顶 / 静音按钮按当前状态给出反向动作", () => {
    const props = renderList([node({ source: "manual", pinned: true, muted: true })]);
    expect(screen.getByRole("button", { name: "删除" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "取消置顶" }));
    expect(props.onPin).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消静音" }));
    expect(props.onMute).toHaveBeenCalled();
  });

  it("截断时如实说明「共 N 条、已显示前 M 条」", () => {
    renderList([node()], 940);
    expect(screen.getByText(/共 940 条，已显示前 1 条/)).toBeVisible();
  });

  it("「注入后表现」列三态不同形：无样本 ⇒ 破折号，已自动静音 ⇒ 形状 + 文字双编码（W-C / I-8）", () => {
    renderList([node()]);
    expect(screen.getByTestId("experience-list-utility")).toHaveTextContent("—");
    cleanup();

    renderList([node({
      utility: { injected: 21, scored: 18, meanNetAfter: -8.4, tAfter: -2.9,
                 agreement: 0.71, claimSign: 1, ineffective: true },
      muted: true, muteReason: "ineffective",
    } as Partial<ExperienceNode>)]);
    const cell = screen.getByTestId("experience-list-utility");
    expect(cell).toHaveTextContent("已自动静音");
    expect(cell).toHaveTextContent("注入 21");
    expect(cell).toHaveTextContent("-8.4bps");
    // 颜色绝不单独承载语义：失效的行在这一格左侧带一条竖线。
    expect(cell).toHaveStyle({ borderLeft: "2px solid var(--warn)" });
    // J-5 / G-13：共现不是因果。
    expect(cell.textContent ?? "").not.toMatch(/贡献|带来|因此|导致/);
  });

  it("点击陈述打开详情抽屉", () => {
    const props = renderList([node()]);
    fireEvent.click(screen.getByRole("button", { name: /高波动/ }));
    expect(props.onOpen).toHaveBeenCalled();
  });
});
