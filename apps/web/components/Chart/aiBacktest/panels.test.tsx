import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AcceptanceBacktestSection } from "./panels";
import type { AiBacktestAiStats } from "@/lib/aiBacktestTypes";

// 回测结果面板的验收区（ai-trade-horizon-acceptance-loop §6.2）。
// 与运行期看板 / 历史详情**共用同一对组件**；此处只守挂载与门控，卡片内部行为由各自的测试守。

const BASE: AiBacktestAiStats = {
  decisionRounds: 10,
  actionCounts: { open_long: 5, close: 5 },
  holdRate: 0,
  avgConfidence: 0.8,
  degradedRounds: 0,
  timeoutRounds: 0,
  summaryCount: 1,
  decisionEveryBars: 2,
  plannedRounds: 10,
  reachedRoundCap: false,
};

const CARD: NonNullable<AiBacktestAiStats["horizonAcceptance"]> = {
  profileId: "short_swing",
  title: "短波段",
  verdict: "fail",
  sampleFloor: 20,
  roundTrips: 25,
  truncatedSamples: 1,
  sampleNote: "样本 25 笔",
  rows: [
    {
      key: "netAfterFunding", label: "资金费后净收益", verdict: "fail", value: -2.61,
      unit: "usd", display: "-2.61 USDT", target: "> 0", basis: "settled+accrued",
    },
  ],
  diagnostics: [],
};

const EXITS: NonNullable<AiBacktestAiStats["exits"]> = {
  totalExits: 25,
  bySource: [{ source: "agent", count: 25, share: 1, netUsd: -2.4, winRate: 0.24 }],
};

afterEach(cleanup);

describe("AcceptanceBacktestSection", () => {
  it("两键都缺（老结果）⇒ 整段不出现", () => {
    const { container } = render(<AcceptanceBacktestSection aiStats={BASE} />);
    expect(container.firstChild).toBeNull();
  });

  it("有验收卡即渲染，并带资金费口径免责（回测资金费不进权益曲线）", () => {
    render(
      <AcceptanceBacktestSection
        aiStats={{ ...BASE, horizonAcceptance: CARD, exits: EXITS }}
      />,
    );
    expect(screen.getByTestId("ai-horizon-acceptance")).toBeInTheDocument();
    expect(screen.getByTestId("ai-exit-source-card")).toBeInTheDocument();
    expect(screen.getByTestId("bt-acceptance-section")).toHaveTextContent(
      "未计入权益曲线",
    );
  });

  it("未装配周期契约的对照回测：卡片缺席但退出来源照常渲染（[P0-4]）", () => {
    render(<AcceptanceBacktestSection aiStats={{ ...BASE, exits: EXITS }} />);
    expect(screen.queryByTestId("ai-horizon-acceptance")).toBeNull();
    expect(screen.getByTestId("ai-exit-source-card")).toBeInTheDocument();
  });
});
