import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IntelAlert } from "@/lib/intelTypes";
import IntelAlertRow from "./IntelAlertRow";

const NOW_MS = 1_800_000_000_000;
const NOW_S = NOW_MS / 1000;

function alert(over: Partial<IntelAlert> = {}): IntelAlert {
  return {
    id: 1, instId: "BTC-USDT-SWAP", groupId: "g", itemId: 41, roundId: 7,
    createdTs: NOW_S - 30, severity: 0.82, fired: true, suppressedCode: "",
    suppressedText: "", outcome: "", outcomeText: "", outcomeTs: 0, runId: 0, roundNo: 0,
    headline: "某交易所热钱包异常出金 1.2 万枚 ETH", category: "risk", tier: "ultra",
    stance: "bearish", impact: "high", domainCount: 3, quoted: true, confidence: 0.78,
    ref: "I02", inDigest: true, reasons: {},
    ...over,
  };
}

function group(over: Partial<IntelAlert> = {}, count = 1) {
  return { key: "k", head: alert(over), count };
}

afterEach(cleanup);

describe("IntelAlertRow · 三态", () => {
  it("等待响应：已告警但库里还没有结果", () => {
    render(<IntelAlertRow group={group()} nowMs={NOW_MS} pendingTtlS={180} />);
    expect(screen.getByTestId("intel-alert-row")).toHaveAttribute("data-state", "waiting");
    expect(screen.getByText("已告警 · 等待会话响应")).toBeInTheDocument();
    expect(screen.getByText(/严重度 0.82/)).toBeInTheDocument();
  });

  it("超时：**渲染期判定，不回写库**——一个只为了让界面好看的写操作会与真正的回写竞态", () => {
    render(<IntelAlertRow group={group({ createdTs: NOW_S - 9999 })} nowMs={NOW_MS}
      pendingTtlS={180} />);
    expect(screen.getByTestId("intel-alert-row")).toHaveAttribute("data-state", "expired");
    expect(screen.getByText("已告警 · 会话未在时限内响应")).toBeInTheDocument();
  });

  it("未告警：后端下发的中文抑制文案 + 灰行；连续同码折叠成一行 + 计数", () => {
    render(<IntelAlertRow
      group={group({ fired: false, suppressedCode: "impact_below_bar",
        suppressedText: "影响力未达门槛" }, 7)}
      nowMs={NOW_MS} pendingTtlS={180} />);
    expect(screen.getByText(/未告警 · 影响力未达门槛/)).toBeInTheDocument();
    expect(screen.getByTestId("intel-alert-fold")).toHaveTextContent("×7");
    // 未告警的行不展示严重度等判据明细（它们只会给一条「什么都没发生」的行添噪音）。
    expect(screen.queryByText(/严重度/)).toBeNull();
  });

  it("已唤醒：可点击跳到那一轮决策轨迹", () => {
    const onOpenRound = vi.fn();
    render(<IntelAlertRow
      group={group({ outcome: "woke", outcomeText: "已唤醒一轮决策", runId: 3, roundNo: 47 })}
      nowMs={NOW_MS} pendingTtlS={180} onOpenRound={onOpenRound} />);
    expect(screen.getByText("已告警 · 已唤醒一轮决策")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("intel-alert-jump"));
    expect(onOpenRound).toHaveBeenCalledWith(3, 47);
  });

  it("没有跳转回调时不渲染一个假的可点按钮", () => {
    render(<IntelAlertRow group={group({ outcome: "woke", runId: 3, roundNo: 47 })}
      nowMs={NOW_MS} pendingTtlS={180} />);
    expect(screen.queryByTestId("intel-alert-jump")).toBeNull();
  });

  it("空轮的行没有标题，但仍然说得出「今天为什么没告警」", () => {
    render(<IntelAlertRow
      group={group({ fired: false, itemId: 0, headline: "", suppressedCode: "no_candidate",
        suppressedText: "本轮没有任何新情报" })}
      nowMs={NOW_MS} pendingTtlS={180} />);
    expect(screen.getByText(/本轮没有任何新情报/)).toBeInTheDocument();
  });

  it("R-15 的可见落点：已告警但那一条根本没进情报段时如实说出来", () => {
    render(<IntelAlertRow group={group({ inDigest: false })} nowMs={NOW_MS} pendingTtlS={180} />);
    expect(screen.getByText(/未进入情报段/)).toBeInTheDocument();
  });

  it("**没有编号不等于不在情报段**：cite 关掉时全库都没有编号，这句话一条都不该出现", () => {
    // 这正是后端 P0-3 花了整节去禁止的那次混淆（「有 ref」≠「在 prompt 里」）。用 `!ref` 当
    // 判据，会让每一条告警行都挂上一句关于数据的假话。
    render(<IntelAlertRow group={group({ ref: "", inDigest: true })} nowMs={NOW_MS}
      pendingTtlS={180} />);
    expect(screen.queryByText(/未进入情报段/)).toBeNull();
  });

  it("拿不到跳转回调时，「唤醒了第几轮」仍然要说出来（只是不画成按钮）", () => {
    // G-11 的验收事实住在 `runId` / `roundNo` 上。不给回调就整段不渲染，等于把台账上已有的
    // 那个事实从界面上抹掉——而这个弹窗今天就没有跳转轨迹的能力。
    render(<IntelAlertRow group={group({ outcome: "woke", outcomeText: "已唤醒一轮决策",
      runId: 12, roundNo: 47 })} nowMs={NOW_MS} pendingTtlS={180} />);
    expect(screen.getByTestId("intel-alert-round")).toHaveTextContent("第 47 轮");
    expect(screen.queryByTestId("intel-alert-jump")).toBeNull();
  });
});
