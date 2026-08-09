import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TurnoverBackstopCard from "./TurnoverBackstopCard";
import type { TurnoverBackstopMember } from "./TurnoverBackstopCard";
import type {
  AiTradeBackstopRepairWire,
  AiTradeBackstopWire,
  AiTradeTurnoverWire,
} from "@/lib/aiTradeTypes";

function turnover(over: Partial<AiTradeTurnoverWire> = {}): AiTradeTurnoverWire {
  return {
    cooldownActive: false,
    cooldownRemainingSeconds: null,
    cooldownSide: null,
    cooldownScope: "same_side",
    cooldownSeconds: 3600,
    budgetEnabled: true,
    budgetScope: "session",
    tokens: 3,
    capacity: 3,
    perDay: 6,
    nextTokenSeconds: null,
    blocked: false,
    reverseWouldDowngrade: false,
    reentryVetoes: 0,
    budgetVetoes: 0,
    opensCounted: 0,
    ...over,
  };
}

function backstop(over: Partial<AiTradeBackstopWire> = {}): AiTradeBackstopWire {
  return {
    basis: "exchange",
    unavailableReason: null,
    checkedAgoSeconds: 42,
    stopLossPx: 62140,
    takeProfitPx: null,
    hasStop: true,
    coveredSz: 3,
    positionSz: 3,
    coverageRatio: 1,
    orders: 1,
    localStopLossPx: 62140,
    localTakeProfitPx: 68000,
    mismatch: false,
    ...over,
  };
}

function repair(over: Partial<AiTradeBackstopRepairWire> = {}): AiTradeBackstopRepairWire {
  return {
    mode: "plan",
    state: "planned",
    blockedReason: null,
    plannedStopPx: 62140,
    plannedSz: 3,
    coveredSz: 0,
    positionSz: 3,
    attemptsEpoch: 0,
    maxPerEpoch: 2,
    attemptsSession: 0,
    maxPerSession: 8,
    coolingSeconds: null,
    lastResult: null,
    ...over,
  };
}

function member(over: Partial<TurnoverBackstopMember> = {}): TurnoverBackstopMember {
  return { symbol: "BTC-USDT-SWAP", turnover: turnover(), backstop: backstop(), ...over };
}

describe("TurnoverBackstopCard", () => {
  it("两键全缺 ⇒ 整卡不渲染（绝不在没有契约的会话上挂一排 0）", () => {
    const { container } = render(
      <TurnoverBackstopCard members={[{ symbol: "BTC-USDT-SWAP" }]} />,
    );
    expect(container.innerHTML).toBe("");
    const empty = render(<TurnoverBackstopCard members={[]} />);
    expect(empty.container.innerHTML).toBe("");
  });

  it("mismatch 用红色并明说没有任何持久化兜底（本轮存在的全部理由）", () => {
    render(
      <TurnoverBackstopCard
        members={[member({ backstop: backstop({ hasStop: false, coverageRatio: 0, mismatch: true }) })]}
      />,
    );
    const row = screen.getByTestId("ai-turnover-row-backstop");
    expect(row.textContent).toContain("没有任何持久化兜底");
    expect(row.innerHTML).toContain("var(--short)");
  });

  it("「未核实」与「覆盖未知」都用灰色，绝不用黄色（黄色会被读成「快好了」）", () => {
    for (const wire of [
      backstop({ basis: "local", unavailableReason: "not_run", stopLossPx: null }),
      backstop({ coverageRatio: null, coveredSz: null }),
    ]) {
      const { unmount } = render(<TurnoverBackstopCard members={[member({ backstop: wire })]} />);
      const row = screen.getByTestId("ai-turnover-row-backstop");
      expect(row.innerHTML).toContain("var(--muted)");
      expect(row.innerHTML).not.toContain("var(--short)");
      expect(row.innerHTML).not.toMatch(/warn|yellow|amber/i);
      unmount();
    }
  });

  it("零样本不编数：无冷却契约与未启用预算时不渲染这两行", () => {
    render(
      <TurnoverBackstopCard
        members={[
          member({
            turnover: turnover({ cooldownSeconds: null, budgetEnabled: false }),
            backstop: undefined,
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId("ai-turnover-row-cooldown")).toBeNull();
    expect(screen.queryByTestId("ai-turnover-row-budget")).toBeNull();
    expect(screen.queryByTestId("ai-turnover-backstop-card")).toBeNull();
  });

  it("多标的分组：按成员渲染、带标的名，**绝不跨成员求和**", () => {
    render(
      <TurnoverBackstopCard
        members={[
          member({ symbol: "BTC-USDT-SWAP", turnover: turnover({ tokens: 2 }) }),
          member({ symbol: "ETH-USDT-SWAP", turnover: turnover({ tokens: 1 }) }),
        ]}
      />,
    );
    expect(screen.getByTestId("ai-turnover-BTC-USDT-SWAP")).toBeTruthy();
    expect(screen.getByTestId("ai-turnover-ETH-USDT-SWAP")).toBeTruthy();
    expect(screen.getByText("BTC-USDT-SWAP")).toBeTruthy();
    // 求和会得到 3 / 6，它不对应任何一条判定；两行必须各自是 2 / 3 与 1 / 3。
    const values = screen.getAllByTestId("ai-turnover-row-budget").map((n) => n.textContent);
    expect(values.some((t) => t?.includes("2.0 / 3"))).toBe(true);
    expect(values.some((t) => t?.includes("1.0 / 3"))).toBe(true);
    expect(values.some((t) => t?.includes("3.0 / 6"))).toBe(false);
  });

  it("最紧要优先：mismatch 的成员排在正常成员之前", () => {
    render(
      <TurnoverBackstopCard
        members={[
          member({ symbol: "OK-SWAP" }),
          member({
            symbol: "BAD-SWAP",
            backstop: backstop({ hasStop: false, coverageRatio: 0, mismatch: true }),
          }),
        ]}
      />,
    );
    const blocks = screen.getAllByTestId(/^ai-turnover-[A-Z]/);
    expect(blocks[0].getAttribute("data-testid")).toBe("ai-turnover-BAD-SWAP");
  });

  it("卡底如实写明本层只约束「开」，且反手的平仓腿一定执行", () => {
    render(<TurnoverBackstopCard members={[member()]} />);
    const card = screen.getByTestId("ai-turnover-backstop-card");
    expect(card.textContent).toContain("只约束建立新敞口的那一腿");
    expect(card.textContent).toContain("平仓腿一定会执行");
    expect(card.textContent).toContain("本会话");
  });
});

describe("TurnoverBackstopCard · 兜底自愈第五行（backstop-repair §2.4.2）", () => {
  it("U-17 `repair` 键缺席 ⇒ 第五行不渲染（整层未装配的会话上不挂一个空壳）", () => {
    render(<TurnoverBackstopCard members={[member()]} />);
    expect(screen.queryByTestId("ai-turnover-row-repair")).toBeNull();
  });

  it("U-18 `idle` 同样不渲染——「一切正常」不需要占一行", () => {
    render(
      <TurnoverBackstopCard
        members={[member({ backstop: backstop({ repair: repair({ state: "idle" }) }) })]}
      />,
    );
    expect(screen.queryByTestId("ai-turnover-row-repair")).toBeNull();
  });

  it("`planned` ⇒ 第五行在场，且带一颗「立即补挂」按钮", () => {
    const onRepair = vi.fn();
    render(
      <TurnoverBackstopCard
        members={[member({ backstop: backstop({ hasStop: false, coverageRatio: 0, repair: repair() }) })]}
        onRepair={onRepair}
      />,
    );
    expect(screen.getByTestId("ai-turnover-row-repair").textContent).toContain("方案就绪");
    fireEvent.click(screen.getByTestId("ai-turnover-repair-BTC-USDT-SWAP"));
    expect(onRepair).toHaveBeenCalledWith("BTC-USDT-SWAP");
  });

  it("不传 `onRepair` ⇒ 不渲染按钮（历史页这类只读场景不该出现一个点了没反应的按钮）", () => {
    render(
      <TurnoverBackstopCard
        members={[member({ backstop: backstop({ hasStop: false, coverageRatio: 0, repair: repair() }) })]}
      />,
    );
    expect(screen.queryByTestId("ai-turnover-repair-BTC-USDT-SWAP")).toBeNull();
  });

  it("U-19 请求态：点击后禁用且文案变「补挂中…」（态由父组件传入，卡片保持无状态）", () => {
    render(
      <TurnoverBackstopCard
        members={[member({ backstop: backstop({ hasStop: false, coverageRatio: 0, repair: repair() }) })]}
        onRepair={vi.fn()}
        repairPending={{ "BTC-USDT-SWAP": { pending: true } }}
      />,
    );
    const button = screen.getByTestId("ai-turnover-repair-BTC-USDT-SWAP") as HTMLButtonElement;
    expect(button.textContent).toBe("补挂中…");
    expect(button.disabled).toBe(true);
  });

  it("刚完成 ⇒ 按钮禁用并把结果写进本行 detail（失败时显示中文标签，不显示裸码）", () => {
    render(
      <TurnoverBackstopCard
        members={[member({ backstop: backstop({ hasStop: false, coverageRatio: 0, repair: repair() }) })]}
        onRepair={vi.fn()}
        repairPending={{
          "BTC-USDT-SWAP": { result: { ok: false, message: "现价已越过本地止损价（51008）" } },
        }}
      />,
    );
    const button = screen.getByTestId("ai-turnover-repair-BTC-USDT-SWAP") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByTestId("ai-turnover-row-repair").textContent).toContain("现价已越过本地止损价");
  });

  it("`px_through` 不给按钮——重试只会更快地做错事（补一张立刻触发的止损=立刻市价平仓）", () => {
    render(
      <TurnoverBackstopCard
        members={[
          member({
            backstop: backstop({
              hasStop: false,
              coverageRatio: 0,
              repair: repair({ state: "blocked", blockedReason: "px_through" }),
            }),
          }),
        ]}
        onRepair={vi.fn()}
      />,
    );
    expect(screen.getByTestId("ai-turnover-row-repair").textContent).toContain("已暂缓");
    expect(screen.queryByTestId("ai-turnover-repair-BTC-USDT-SWAP")).toBeNull();
  });
});
