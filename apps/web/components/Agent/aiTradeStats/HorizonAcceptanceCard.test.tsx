import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import HorizonAcceptanceCard from "./HorizonAcceptanceCard";
import type { AiTradeAcceptance, AiTradeAcceptanceVerdict } from "@/lib/aiTradeTypes";

// 周期验收卡（ai-trade-horizon-acceptance-loop §6.2）。
// 核心断言只有一条：**样本不足时不许出现任何一个绿色**——那是 L1 放行纪律唯一的视觉保证。

function card(
  verdict: AiTradeAcceptanceVerdict,
  rowVerdict: AiTradeAcceptanceVerdict = verdict,
): AiTradeAcceptance {
  return {
    profileId: "short_swing",
    title: "短波段",
    verdict,
    sampleFloor: 20,
    roundTrips: verdict === "insufficient" ? 16 : 30,
    truncatedSamples: 1,
    sampleNote: verdict === "insufficient" ? "样本 16 / 20 —— 未达样本下限" : "样本 30 笔",
    rows: [
      {
        key: "avgHold", label: "平均持仓时间", verdict: rowVerdict, value: 2140.5,
        unit: "seconds", display: "35m 41s", target: "≥ 12h",
        note: "含 1 笔被区间 / 会话收尾截断的样本",
      },
      {
        key: "netAfterFunding", label: "资金费后净收益", verdict: rowVerdict,
        value: null, unit: "usd", display: "—", target: "> 0",
        note: "资金费口径不可得", basis: "unavailable",
      },
    ],
    diagnostics: ["Agent 主动平仓占 93.8% —— 抢跑退出仍占主导。"],
  };
}

afterEach(cleanup);

describe("HorizonAcceptanceCard", () => {
  it("acceptance 为 null / 无行 ⇒ 整卡不渲染", () => {
    const { container } = render(<HorizonAcceptanceCard acceptance={null} />);
    expect(container.firstChild).toBeNull();
    cleanup();
    const empty = render(
      <HorizonAcceptanceCard acceptance={{ ...card("pass"), rows: [] }} />,
    );
    expect(empty.container.firstChild).toBeNull();
  });

  it("pass / fail / insufficient 三态各自落到 data-verdict 上", () => {
    for (const verdict of ["pass", "fail", "insufficient"] as const) {
      render(<HorizonAcceptanceCard acceptance={card(verdict)} />);
      expect(screen.getByTestId("ai-horizon-acceptance")).toHaveAttribute(
        "data-verdict",
        verdict,
      );
      cleanup();
    }
  });

  it("样本不足时没有任何一行是绿色，且卡底写出样本进度", () => {
    render(<HorizonAcceptanceCard acceptance={card("insufficient")} />);
    const rows = screen.getAllByTestId(/^ai-acceptance-row-/);
    expect(rows.length).toBe(2);
    for (const row of rows) {
      expect(row).toHaveAttribute("data-verdict", "insufficient");
      expect(row.innerHTML).not.toContain("--long");
      expect(row.innerHTML).not.toContain("--success");
    }
    expect(screen.getByTestId("ai-acceptance-sample")).toHaveTextContent("样本 16 / 20");
  });

  it("整卡结论徽标显示中文三态", () => {
    render(<HorizonAcceptanceCard acceptance={card("fail")} />);
    expect(screen.getByTestId("ai-acceptance-verdict")).toHaveTextContent("未通过");
  });

  it("value 为 null 的行渲染后端给的 display 而不是 0", () => {
    render(<HorizonAcceptanceCard acceptance={card("fail")} />);
    const row = screen.getByTestId("ai-acceptance-row-netAfterFunding");
    expect(row).toHaveTextContent("—");
    expect(row).not.toHaveTextContent("0.00");
  });

  it("诊断行照常呈现（它是本卡唯一的「下一步该看哪」提示）", () => {
    render(<HorizonAcceptanceCard acceptance={card("fail")} />);
    expect(screen.getByText(/抢跑退出仍占主导/)).toBeInTheDocument();
  });

  it("多标的分组展开逐标的摘要；单标的 / 缺席 ⇒ 整段不出现（R-10）", () => {
    render(<HorizonAcceptanceCard acceptance={card("fail")} />);
    expect(screen.queryByTestId("ai-acceptance-by-symbol")).toBeNull();
    cleanup();

    render(
      <HorizonAcceptanceCard
        acceptance={{
          ...card("fail"),
          bySymbol: [
            { instId: "BTC-USDT-SWAP", roundTrips: 10, netPnlUsd: 12.5, verdict: "pass" },
            { instId: "ETH-USDT-SWAP", roundTrips: 15, netPnlUsd: -15.1, verdict: "fail" },
          ],
        }}
      />,
    );
    const box = screen.getByTestId("ai-acceptance-by-symbol");
    expect(box).toHaveTextContent("BTC-USDT-SWAP");
    expect(box).toHaveTextContent("ETH-USDT-SWAP");
    // 一个赚一个亏必须都看得见——这正是「被平均成还行」要防的那件事。
    expect(box).toHaveTextContent("+12.50");
    expect(box).toHaveTextContent("-15.10");
  });

  it("单标的分组只有一条 ⇒ 不渲染（与总判定逐字相同，多挂一行只是噪音）", () => {
    render(
      <HorizonAcceptanceCard
        acceptance={{
          ...card("fail"),
          bySymbol: [
            { instId: "BTC-USDT-SWAP", roundTrips: 10, netPnlUsd: 1, verdict: "fail" },
          ],
        }}
      />,
    );
    expect(screen.queryByTestId("ai-acceptance-by-symbol")).toBeNull();
  });
});
