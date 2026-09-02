import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import TrendAlignmentCard from "./TrendAlignmentCard";
import type { AiTradeTrendStats } from "@/lib/aiTradeTypes";

// 顺势 / 逆势卡（execution-trend-verdict §7.5 F-5）。
//
// 三条守卫：
// ① **未装配 / 零样本 ⇒ 整卡不渲染**（绝不在没有数据的会话上挂一排 0）；
// ② **样本不足写「样本 a/b」而不是 0%**；
// ③ **`unknown` 单列一格**，绝不并进任何一侧——归属不明的成交来自接管仓位 / 离线平仓 /
//    重启后续跑，把它摊进两侧就是编数据。

function stats(over: Partial<AiTradeTrendStats> = {}): AiTradeTrendStats {
  return {
    armed: true,
    bar: "15m",
    verdictRounds: { up: 31, down: 44, range: 18, unknown: 2 },
    intents: { aligned: 9, counter: 4, unknown: 3 },
    counterBlocked: { confidence: 3, size: 1 },
    counterPassed: 1,
    bonusLanes: {
      voteApplied: 5,
      allowanceApplied: 2,
      blockedNoNativeVote: 7,
      blockedConflict: 11,
      blockedNotAligned: 4,
    },
    filled: { aligned: 6, counter: 3, unknown: 2 },
    netPnlUsd: { aligned: 41.2, counter: -8.7, unknown: 3.1 },
    minSamples: 8,
    ...over,
  };
}

afterEach(cleanup);

describe("TrendAlignmentCard", () => {
  it("未装配 / 零样本 / 老记录 ⇒ 整卡不渲染", () => {
    const { container } = render(<TrendAlignmentCard stats={null} />);
    expect(container).toBeEmptyDOMElement();
    cleanup();
    const off = render(<TrendAlignmentCard stats={stats({ armed: false })} />);
    expect(off.container).toBeEmptyDOMElement();
    cleanup();
    const empty = render(
      <TrendAlignmentCard stats={stats({ verdictRounds: { up: 0, down: 0, range: 0, unknown: 0 } })} />,
    );
    expect(empty.container).toBeEmptyDOMElement();
  });

  it("第 1 行：本会话基调分布四格齐（回答「15m 究竟有多少轮是偏空的」）", () => {
    render(<TrendAlignmentCard stats={stats()} />);
    const row = screen.getByTestId("ai-trend-rounds");
    expect(row).toHaveTextContent("偏多 31 轮");
    expect(row).toHaveTextContent("偏空 44 轮");
    expect(row).toHaveTextContent("中性 18 轮");
    expect(row).toHaveTextContent("不可用 2 轮");
  });

  it("第 2 行：开仓意愿三格，`unknown` **单列**、绝不并进任何一侧", () => {
    render(<TrendAlignmentCard stats={stats()} />);
    const row = screen.getByTestId("ai-trend-intents");
    expect(row).toHaveTextContent("顺势 9");
    expect(row).toHaveTextContent("逆势 4");
    expect(row).toHaveTextContent("归属不明 3");
  });

  it("第 3 行：拦截与**放行**分开写，两条放宽通路各自可见（RK-1 缓解④ 的读点）", () => {
    render(<TrendAlignmentCard stats={stats()} />);
    const row = screen.getByTestId("ai-trend-gate");
    expect(row).toHaveTextContent("拦下 4 次");
    expect(row).toHaveTextContent("置信度 3");
    expect(row).toHaveTextContent("降档 1");
    // 放行**不是**拦截：把它塞进拦截计数，会让闸计数把一次成功开仓记成一次被拦。
    expect(row).toHaveTextContent("放行 1 次");
    expect(row).toHaveTextContent("追加票 5 次");
    expect(row).toHaveTextContent("放宽反向票上限 2 次");
  });

  it("F-5：样本 < minSamples 时写「样本 3/8，尚不足以下结论」而**不是** 0%", () => {
    render(
      <TrendAlignmentCard
        stats={stats({ filled: { aligned: 2, counter: 1, unknown: 9 } })}
      />,
    );
    const line = screen.getByTestId("ai-trend-sample");
    expect(line).toHaveTextContent("样本 3/8，尚不足以下结论");
    expect(line.textContent ?? "").not.toContain("%");
    expect(screen.queryByTestId("ai-trend-pnl")).toBeNull();
  });

  it("样本足够时才画成绩行，三格净额各自可见（`unknown` 仍单列）", () => {
    render(<TrendAlignmentCard stats={stats()} />);
    const row = screen.getByTestId("ai-trend-pnl");
    expect(row).toHaveTextContent("顺势 6 笔 +41.20 USDT");
    expect(row).toHaveTextContent("逆势 3 笔 -8.70 USDT");
    expect(row).toHaveTextContent("归属不明 2 笔 +3.10 USDT");
    expect(screen.queryByTestId("ai-trend-sample")).toBeNull();
  });

  it("卡头写明判定级别——它是本特性的产品承诺（判在用户所选的那个级别上）", () => {
    render(<TrendAlignmentCard stats={stats()} />);
    expect(screen.getByTestId("ai-trend-alignment-card")).toHaveTextContent("判定级别 15m");
  });
});
