import { describe, expect, it } from "vitest";

import {
  attributeByWake,
  formatWakeWinRate,
  hasWakeAttribution,
  UNKNOWN_WAKE_KIND,
  WAKE_ATTRIBUTION_MIN_SAMPLES,
} from "./aiTradeWakeAttribution";
import type { RoundTrip } from "./autoTradePnl";
import type { Trade, TriggerExplain, TriggerExplainKind } from "./types";

// 轴 D 纯函数用例（ai-trade-wake-scheduler-closed-loop §6.1 T-16/T-17/T-18）。
// 覆盖：按**开仓腿**归因、unknown 桶恒排末位、最小样本门禁诚实降级、反手往返不被平仓腿带偏。

function explain(kind: TriggerExplainKind, wakeKind?: string): TriggerExplain {
  return {
    schema: 1,
    kind,
    side: kind === "reverse" ? "close" : "long",
    headline: "h",
    metrics: [],
    meta: wakeKind ? { wakeKind } : {},
  };
}

function trade(o: Partial<Trade> & Pick<Trade, "ts" | "side">): Trade {
  return {
    id: o.id,
    runId: 1,
    ts: o.ts,
    side: o.side,
    price: o.price ?? 100,
    qty: 1,
    fee: 0,
    pnl: o.pnl ?? 0,
    positionAfter: 0,
    reason: "",
    explain: o.explain ?? null,
  };
}

function trip(o: {
  pnlUsd: number;
  entryWake?: string;
  exitWake?: string;
  exitKind?: TriggerExplainKind;
}): RoundTrip {
  return {
    instId: "BTC-USDT-SWAP",
    direction: "long",
    entry: trade({ ts: 1, side: "long", explain: explain("atomic", o.entryWake) }),
    exit: trade({
      ts: 2,
      side: "close",
      pnl: o.pnlUsd,
      explain: explain(o.exitKind ?? "atomic", o.exitWake),
    }),
    pnlUsd: o.pnlUsd,
    pnlPct: null,
    feeUsd: 0,
    grossPnlUsd: o.pnlUsd,
    openedTs: 1,
    closedTs: 2,
    isWin: o.pnlUsd > 0,
  };
}

function wins(kind: string, count: number, each = 10): RoundTrip[] {
  return Array.from({ length: count }, () => trip({ pnlUsd: each, entryWake: kind }));
}

describe("attributeByWake (轴 D：按开仓唤醒来源切胜率)", () => {
  it("groups by the entry leg's wakeKind and sums net PnL per bucket", () => {
    const rows = attributeByWake([
      ...wins("candle_close", 4),
      trip({ pnlUsd: -6, entryWake: "candle_close" }),
      ...wins("price_breakout", 2, 3),
    ]);
    const candle = rows.find((r) => r.kind === "candle_close")!;
    expect(candle.count).toBe(5);
    expect(candle.wins).toBe(4);
    expect(candle.netUsd).toBe(34);
    expect(candle.winRate).toBeCloseTo(0.8);
    expect(candle.expectancyUsd).toBeCloseTo(6.8);
    expect(candle.label).toBe("K 线收盘 · 信号板已重算");
  });

  it("puts round trips without a wakeKind into the unknown bucket and sorts it last", () => {
    const rows = attributeByWake([
      trip({ pnlUsd: 1 }),
      trip({ pnlUsd: 2 }),
      trip({ pnlUsd: 3 }),
      ...wins("signal_flip", 1),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["signal_flip", UNKNOWN_WAKE_KIND]);
    expect(rows[1].label).toBe("未记录来源");
    expect(rows[1].count).toBe(3);
  });

  it("attributes a reverse round trip by its entry leg, never by the closing leg", () => {
    // `reverse` 是**平仓腿**的解释、同样带 wakeKind。用它归因就等于按「离场时是什么叫醒的」切表
    // ——答非所问，而且会把保护层救回来的单整类打进 unknown 桶，恰好抹掉最值得复盘的那一类。
    const rows = attributeByWake([
      trip({ pnlUsd: 5, entryWake: "candle_close", exitWake: "signal_flip", exitKind: "reverse" }),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["candle_close"]);
    expect(rows.find((r) => r.kind === "signal_flip")).toBeUndefined();
  });

  it("sorts buckets by sample count descending", () => {
    const rows = attributeByWake([...wins("heartbeat", 2), ...wins("bar_advanced", 6)]);
    expect(rows.map((r) => r.kind)).toEqual(["bar_advanced", "heartbeat"]);
  });

  it("keeps an unmirrored backend kind visible rather than swallowing it into unknown", () => {
    // 后端未来新增而前端尚未镜像的 kind 原样显示——静默吞掉会让一整类唤醒凭空消失。
    const rows = attributeByWake([trip({ pnlUsd: 1, entryWake: "future_kind" })]);
    expect(rows[0].kind).toBe("future_kind");
    expect(rows[0].label).toBe("future_kind");
  });
});

describe("最小样本门禁（T-17：绝不用 2 笔样本冒充统计结论）", () => {
  it("returns null (never 0) for win rate and expectancy below the sample floor", () => {
    const rows = attributeByWake(wins("candle_close", WAKE_ATTRIBUTION_MIN_SAMPLES - 1));
    expect(rows[0].count).toBe(4);
    expect(rows[0].winRate).toBeNull();
    expect(rows[0].expectancyUsd).toBeNull();
    expect(rows[0].netUsd).toBe(40); // 净额是事实、照常给；只有**推断量**才降级
  });

  it("starts reporting exactly at the sample floor", () => {
    const rows = attributeByWake(wins("candle_close", WAKE_ATTRIBUTION_MIN_SAMPLES));
    expect(rows[0].winRate).toBe(1);
    expect(formatWakeWinRate(rows[0])).toBe("100%");
  });

  it("renders an honest 样本不足 label instead of a fake percentage", () => {
    const rows = attributeByWake(wins("candle_close", 3));
    expect(formatWakeWinRate(rows[0])).toBe("样本不足（n=3）");
  });
});

describe("hasWakeAttribution (T-18：无归因即整卡不渲染)", () => {
  it("is false when every round trip lacks a wakeKind", () => {
    expect(hasWakeAttribution(attributeByWake([trip({ pnlUsd: 1 }), trip({ pnlUsd: 2 })]))).toBe(false);
  });

  it("is false for an empty ledger", () => {
    expect(hasWakeAttribution(attributeByWake([]))).toBe(false);
  });

  it("is true as soon as one round trip carries a wakeKind", () => {
    expect(hasWakeAttribution(attributeByWake([trip({ pnlUsd: 1, entryWake: "heartbeat" })]))).toBe(
      true,
    );
  });
});
