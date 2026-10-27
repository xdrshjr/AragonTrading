import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AiTradeEvidenceSide, AiTradeEvidenceStatus } from "@/lib/aiTradeEvidence";
import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";
import AiTradeEvidenceRow from "./AiTradeEvidenceRow";

/**
 * 持仓行「实证准入」（evidence-gated-capital §3.5.1 / §7.5）：整键缺席 ⇒ 不渲染；影子徽标只看
 * 后端下发的 `gating`；`snapshotError` 必须有落点（RC-7）。
 */
afterEach(cleanup);

const LONG: AiTradeEvidenceSide = {
  state: "unproven", basis: "bucket", n: 7, meanBps: 1.3, lcbBps: -2.1, ucbBps: 4.7,
  winRate: 0.57, gating: "shadow", openShadow: true, sessionShadows: 9, sessionTraded: 0,
};
const SHORT: AiTradeEvidenceSide = {
  state: "disproven", basis: "pooled", n: 3, meanBps: -6.2, lcbBps: -9, ucbBps: -1.1,
  winRate: 0.33, gating: "live", openShadow: false, sessionShadows: 5, sessionTraded: 3,
};

function evidence(over: Partial<AiTradeEvidenceStatus> = {}): AiTradeEvidenceStatus {
  return {
    policy: "strict", minSamples: 10, refPolicy: "ref-v1:sl2/tp4",
    sides: { long: LONG, short: SHORT },
    session: { intents: 42, shadows: 14, repeats: 28, matured: 11, traded: 3, voided: 0, dropped: 1 },
    snapshotError: false,
    ...over,
  };
}

function member(ev?: AiTradeEvidenceStatus) {
  return {
    instId: "ETH-USDT-SWAP",
    session: { positionSide: "flat", ...(ev ? { evidence: ev } : {}) },
    position: null,
  } as unknown as AiTradeGroupMemberState;
}

describe("AiTradeEvidenceRow", () => {
  it("evidence 整键缺席 ⇒ 不渲染", () => {
    render(<AiTradeEvidenceRow member={member()} />);
    expect(screen.queryByTestId("ai-trade-evidence")).toBeNull();
  });

  it("空仓也渲染（它回答的是下一次开仓会不会下真单），两段按多、空顺序", () => {
    render(<AiTradeEvidenceRow member={member(evidence())} />);
    const row = screen.getByTestId("ai-trade-evidence");
    expect(row).toHaveTextContent("实证");
    expect(screen.getByTestId("ai-evidence-side-long")).toHaveTextContent("多未证实7/10");
    expect(screen.getByTestId("ai-evidence-side-short")).toHaveTextContent(
      "空已证伪均值 −6.2 bps（上界 −1.1）",
    );
  });

  it("影子徽标只在 gating=shadow 时出现，title 解释何时恢复", () => {
    render(<AiTradeEvidenceRow member={member(evidence())} />);
    const badge = screen.getByTestId("ai-evidence-shadow-long");
    expect(badge).toHaveTextContent("影子");
    expect(badge).toHaveAttribute(
      "title",
      "该方向的开仓只记录、不下真实委托，证据下界转正后自动恢复",
    );
    expect(screen.queryByTestId("ai-evidence-shadow-short")).toBeNull();
  });

  it("行 title 带会话级合计与参考规则版本", () => {
    render(<AiTradeEvidenceRow member={member(evidence())} />);
    const title = screen.getByTestId("ai-trade-evidence").getAttribute("title") ?? "";
    expect(title).toContain("开仓意愿 42");
    expect(title).toContain("真单 3");
    expect(title).toContain("未落库 1");
    expect(title).toContain("ref-v1:sl2/tp4");
  });

  it("snapshotError ⇒ 显示「证据读取失败，按未证实处理」；缺席方向不出段", () => {
    render(
      <AiTradeEvidenceRow
        member={member(evidence({ snapshotError: true, sides: { long: LONG } }))}
      />,
    );
    expect(screen.getByTestId("ai-trade-evidence-snapshot-error")).toHaveTextContent(
      "证据读取失败，按未证实处理",
    );
    expect(screen.queryByTestId("ai-evidence-side-short")).toBeNull();
  });
});
