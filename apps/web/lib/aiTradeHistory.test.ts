import { describe, expect, it } from "vitest";
import { mergeMemberEquity } from "@/components/Agent/aiTradeStats/AiTradeStatsDetail";
import { buildLedger, buildRoundTrips, summarizeRoundTrips } from "@/lib/autoTradePnl";
import type { AiTradeHistoryMember } from "@/lib/aiTradeHistoryTypes";
import type { Trade } from "@/lib/types";

// trade_out 形状的 wire dict：出 pnl（后端已实现）、**无** pnlPct / seeded（P2-4）。
function wire(o: Partial<Trade> & Pick<Trade, "ts" | "side" | "price">): Trade {
  return {
    id: o.id,
    runId: o.runId ?? 1,
    ts: o.ts,
    side: o.side,
    price: o.price,
    qty: o.qty ?? 1,
    fee: o.fee ?? 0,
    pnl: o.pnl ?? 0,
    positionAfter: o.positionAfter ?? 0,
    reason: o.reason ?? "",
    explain: null,
  };
}

// 组：BTC（杠杆3，realized +8）+ ETH（杠杆5，realized +10）；总预算 200。
const BTC_TRADES: Trade[] = [
  wire({ id: 1, ts: 10, side: "long", price: 100 }),
  wire({ id: 2, ts: 20, side: "close", price: 110, pnl: 12 }),
  wire({ id: 3, ts: 30, side: "long", price: 100 }),
  wire({ id: 4, ts: 40, side: "close", price: 96, pnl: -4 }),
];
const ETH_TRADES: Trade[] = [
  wire({ id: 5, ts: 15, side: "short", price: 50 }),
  wire({ id: 6, ts: 25, side: "close", price: 40, pnl: 10 }),
];

function member(o: Partial<AiTradeHistoryMember> & Pick<AiTradeHistoryMember, "instId">): AiTradeHistoryMember {
  return {
    runId: o.runId ?? 1,
    instId: o.instId,
    leverage: o.leverage ?? 3,
    budgetUsd: o.budgetUsd ?? 100,
    allocationPct: o.allocationPct ?? 50,
    strategyNames: o.strategyNames ?? ["动量"],
    status: o.status ?? "stopped",
    metrics: o.metrics ?? {},
    trades: o.trades ?? [],
  };
}

describe("aiTradeHistory front-end recompute (口径逐值一致门禁)", () => {
  it("recomputes detail members == backend summary (逐值吻合)", () => {
    const members = [
      { instId: "BTC-USDT-SWAP", trades: BTC_TRADES, leverage: 3 },
      { instId: "ETH-USDT-SWAP", trades: ETH_TRADES, leverage: 5 },
    ];
    const summary = summarizeRoundTrips(buildLedger(members), 200);
    // backend summary（手算）：realized = 12 - 4 + 10 = 18；returnPct = 18/200；3 笔；胜率 2/3；best 12；worst -4
    expect(summary.totalPnlUsd).toBeCloseTo(18);
    expect(summary.totalReturnPct).toBeCloseTo(0.09);
    expect(summary.count).toBe(3);
    expect(summary.winRate).toBeCloseTo(2 / 3);
    expect(summary.bestUsd).toBeCloseTo(12);
    expect(summary.worstUsd).toBeCloseTo(-4);
  });

  it("derives pnlPct from member.leverage when trade_out lacks pnlPct (P2-4)", () => {
    const btc = buildRoundTrips("BTC-USDT-SWAP", BTC_TRADES, 3);
    const eth = buildRoundTrips("ETH-USDT-SWAP", ETH_TRADES, 5);
    // 缺 pnlPct → derivePct（价格变动% × 杠杆，方向化）；缺 seeded → 不误滤
    expect(btc).toHaveLength(2);
    expect(btc[0].pnlPct).toBeCloseTo(0.3); // long 100→110 × 3
    expect(eth).toHaveLength(1);
    expect(eth[0].pnlPct).toBeCloseTo(1.0); // short 50→40 × 5 = +1.0
    // 消费字段 side/ts/price/pnl 全在，pnlUsd 取真实 exit.pnl
    expect(btc[0].pnlUsd).toBe(12);
    expect(eth[0].pnlUsd).toBe(10);
  });

  it("is safe on empty members / empty trades", () => {
    expect(buildLedger([]).length).toBe(0);
    expect(buildRoundTrips("BTC-USDT-SWAP", [], 3).length).toBe(0);
    const s = summarizeRoundTrips([], 0);
    expect(s.totalReturnPct).toBe(0);
    expect(s.winRate).toBe(0);
  });
});

describe("mergeMemberEquity (P1-5 多成员权益合并)", () => {
  it("union timeline + forward-fill sum; drawdown recomputed on combined curve (≠ 成员回撤之和)", () => {
    const members = [
      member({
        instId: "BTC-USDT-SWAP",
        budgetUsd: 100,
        metrics: {
          equityDigest: [
            { ts: 10, equity: 100, drawdown: 0 },
            { ts: 30, equity: 120, drawdown: 0 }, // 单调上行，成员自身无回撤
          ],
        },
      }),
      member({
        instId: "ETH-USDT-SWAP",
        budgetUsd: 100,
        metrics: {
          equityDigest: [
            { ts: 20, equity: 100, drawdown: 0 },
            { ts: 40, equity: 80, drawdown: -0.2 }, // 成员自身回撤 -0.20
          ],
        },
      }),
    ];
    const merged = mergeMemberEquity(members);
    // 并集时间轴 [10,20,30,40]；前值填充（首样本前用预算基线 100）
    expect(merged.map((p) => p.ts)).toEqual([10, 20, 30, 40]);
    expect(merged.map((p) => p.equity)).toEqual([200, 200, 220, 200]);
    // 组合回撤在求和曲线上重算：末点 200/220-1 ≈ -0.0909，绝非相加成员 drawdown(-0.20)
    expect(merged[3].drawdown).toBeCloseTo(-0.0909, 3);
    expect(merged[3].drawdown).not.toBeCloseTo(-0.2, 3);
  });

  it("returns [] when all members lack equityDigest (详情据此降级为不画图)", () => {
    const members = [member({ instId: "BTC-USDT-SWAP", metrics: {} })];
    expect(mergeMemberEquity(members)).toEqual([]);
  });
});
