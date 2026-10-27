import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AiTradeEvidenceSide, AiTradeEvidenceStatus } from "@/lib/aiTradeEvidence";
import EvidenceCard from "./EvidenceCard";

/**
 * 统计页「实证准入」卡（evidence-gated-capital §3.5.2 / §7.5）：「本会话」列读按方向的
 * `sessionTraded / sessionShadows / openShadow`（RC-7）；区间条零线居中；窄屏隐藏类名冻结。
 */
afterEach(cleanup);

const LONG: AiTradeEvidenceSide = {
  state: "proven", basis: "bucket", n: 12, meanBps: 3.1, lcbBps: 0.4, ucbBps: 5.8,
  winRate: 0.62, gating: "live", openShadow: true, sessionShadows: 11, sessionTraded: 3,
};
const SHORT: AiTradeEvidenceSide = {
  state: "disproven", basis: "pooled", n: 1, meanBps: -6.2, lcbBps: -9, ucbBps: -1.1,
  winRate: 0, gating: "shadow", openShadow: false, sessionShadows: 5, sessionTraded: 0,
};

function evidence(over: Partial<AiTradeEvidenceStatus> = {}): AiTradeEvidenceStatus {
  return {
    policy: "strict", minSamples: 10, refPolicy: "ref-v1:x",
    sides: { long: LONG, short: SHORT },
    session: { intents: 1, shadows: 1, repeats: 0, matured: 0, traded: 0, voided: 0, dropped: 0 },
    snapshotError: false,
    ...over,
  };
}

describe("EvidenceCard", () => {
  it("所有成员都未装配 ⇒ 整卡不渲染", () => {
    render(<EvidenceCard members={[{ instId: "ETH-USDT-SWAP", evidence: undefined }]} />);
    expect(screen.queryByTestId("ai-evidence-card")).toBeNull();
  });

  it("每成员每方向一行：标的 · 方向、样本、数值区间、胜率、本会话", () => {
    render(<EvidenceCard members={[{ instId: "ETH-USDT-SWAP", evidence: evidence() }]} />);
    const long = within(screen.getByTestId("ai-evidence-row-ETH-USDT-SWAP-long"));
    expect(long.getByText("ETH 多")).toBeInTheDocument();
    expect(long.getByText("已证实")).toBeInTheDocument();
    expect(long.getByText("12 / 10")).toBeInTheDocument();
    expect(long.queryByTestId("ai-evidence-progress")).toBeNull();   // 攒满后隐藏进度条
    expect(long.getByText("+3.1 bps [+0.4, +5.8]")).toBeInTheDocument();
    expect(long.getByText("62%")).toBeInTheDocument();
    expect(long.getByTestId("ai-evidence-session")).toHaveTextContent("真单 3 · 影子 11 · 进行中 1");
    const short = within(screen.getByTestId("ai-evidence-row-ETH-USDT-SWAP-short"));
    expect(short.getByTestId("ai-evidence-progress")).toBeInTheDocument();
    expect(short.getByText("—")).toBeInTheDocument();                  // n<2 胜率
    expect(short.getByTestId("ai-evidence-session")).toHaveTextContent("真单 0 · 影子 5 · 进行中 0");
  });

  it("basis=pooled ⇒ 注记「由同方向合并样本判定」，只挂在那一行", () => {
    render(<EvidenceCard members={[{ instId: "ETH-USDT-SWAP", evidence: evidence() }]} />);
    expect(screen.getAllByTestId("ai-evidence-pooled")).toHaveLength(1);
    expect(
      within(screen.getByTestId("ai-evidence-row-ETH-USDT-SWAP-short"))
        .getByText("由同方向合并样本判定"),
    ).toBeInTheDocument();
  });

  it("区间条零线居中，且窄屏（< 860px）隐藏类名冻结", () => {
    render(<EvidenceCard members={[{ instId: "ETH-USDT-SWAP", evidence: evidence() }]} />);
    const bars = screen.getAllByTestId("ai-evidence-interval");
    expect(bars).toHaveLength(2);
    for (const bar of bars) expect(bar.getAttribute("class")).toContain("max-[860px]:hidden");
    for (const zero of screen.getAllByTestId("ai-evidence-zero")) {
      expect(zero.getAttribute("x1")).toBe("50");
      expect(zero.getAttribute("x2")).toBe("50");
    }
  });

  it("页脚两行口径说明；快照失败的成员在卡内点名", () => {
    render(
      <EvidenceCard
        members={[
          { instId: "ETH-USDT-SWAP", evidence: evidence() },
          { instId: "BTC-USDT-SWAP", evidence: evidence({ snapshotError: true, sides: {} }) },
        ]}
      />,
    );
    expect(screen.getByText(/样本按固定参考离场规则计算/)).toBeInTheDocument();
    expect(screen.getByText(/「实证准入」衡量的是这个方向过去是否真的赚钱/)).toBeInTheDocument();
    expect(screen.getByText("BTC-USDT-SWAP：证据读取失败，按未证实处理")).toBeInTheDocument();
  });
});
