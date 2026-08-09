import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ExitSourceCard from "./ExitSourceCard";
import HorizonAcceptanceCard from "./HorizonAcceptanceCard";
import type { AiTradeExitMix } from "@/lib/aiTradeTypes";

// 退出来源卡（ai-trade-horizon-acceptance-loop §6.2）。
// 最重要的一条：`acceptance == null` 时它**照常渲染**——关掉周期模式跑对照臂是上线观察窗口的
// 必要一臂，把它挂在 acceptance 之下会让对照组记了数据却一个字都看不到（[P0-4]）。

const MIX: AiTradeExitMix = {
  totalExits: 16,
  bySource: [
    {
      source: "agent", count: 15, share: 0.9375, netUsd: -2.4741,
      avgHoldSeconds: 2140.5, winRate: 0.2667,
    },
    {
      source: "profitGuard", count: 1, share: 0.0625, netUsd: 1.8655,
      avgHoldSeconds: 9820, winRate: null,
    },
  ],
};

afterEach(cleanup);

describe("ExitSourceCard", () => {
  it("exits 为 null ⇒ 不渲染", () => {
    const { container } = render(<ExitSourceCard exits={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("只有「未记录来源」⇒ 不渲染（老会话不该看到一张全是未记录的表）", () => {
    const { container } = render(
      <ExitSourceCard
        exits={{ totalExits: 5, bySource: [{ source: "unknown", count: 5, share: 1, netUsd: 0 }] }}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("样本 < 5 的来源显示「样本不足」而不是 0% 胜率", () => {
    render(<ExitSourceCard exits={MIX} />);
    const guard = screen.getByTestId("ai-exit-row-profitGuard");
    expect(guard).toHaveTextContent("样本不足");
    expect(screen.getByTestId("ai-exit-row-agent")).toHaveTextContent("26.7%");
  });

  it("acceptance 为 null 时仍渲染（对照臂可观测，[P0-4]）", () => {
    render(
      <>
        <HorizonAcceptanceCard acceptance={null} />
        <ExitSourceCard exits={MIX} />
      </>,
    );
    expect(screen.queryByTestId("ai-horizon-acceptance")).toBeNull();
    expect(screen.getByTestId("ai-exit-source-card")).toBeInTheDocument();
  });

  it("明确写出「不参与验收判定」（否则会诱导出故意不平仓等保护层的劣化行为）", () => {
    render(<ExitSourceCard exits={MIX} />);
    expect(screen.getByTestId("ai-exit-source-card")).toHaveTextContent("不参与验收判定");
  });

  it("每种来源一行 + 中文标签", () => {
    render(<ExitSourceCard exits={MIX} />);
    expect(screen.getByTestId("ai-exit-row-agent")).toHaveTextContent("Agent 主动平仓");
    expect(screen.getByTestId("ai-exit-row-profitGuard")).toHaveTextContent("利润保护层");
  });
});
