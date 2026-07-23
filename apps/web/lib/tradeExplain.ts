import type { FactorContribution, Trade, TriggerExplain, TriggerExplainKind } from "@/lib/types";

/**
 * 自动交易触发解释的前端纯件（auto-trade-explainability §4.3）。
 *
 * 不触 DOM、可单测：类别标签 / 色调映射、因子贡献条几何（归一化宽度、diverging 零轴、top-driver），
 * 以及对无 explain 的 legacy 成交据 `reason` 合成回落解释（保证任意成交展开都有内容，R7/R10）。
 */

const KIND_LABEL: Record<TriggerExplainKind, string> = {
  factor_score: "因子组合",
  atomic: "策略信号",
  risk_exit: "时间止盈",
  reverse: "反手",
  session_stop: "会话止盈/止损",
  external_close: "外部平仓",
  profit_guard: "利润保护",
};

const KIND_TONE: Record<TriggerExplainKind, "long" | "short" | "neutral" | "warn"> = {
  factor_score: "neutral",
  atomic: "neutral",
  risk_exit: "warn",
  reverse: "neutral",
  session_stop: "warn",
  external_close: "warn",
  profit_guard: "warn",
};

const TOP_DRIVER_COUNT = 3;
const TOP_DRIVER_SHARE = 0.15;

/** 类别中文标签（未知 kind 回落「自动交易」，杜绝枚举漂移致空白，R10）。 */
export function explainKindLabel(kind: TriggerExplain["kind"] | undefined): string {
  if (!kind) return "自动交易";
  return KIND_LABEL[kind] ?? "自动交易";
}

/** 类别角标色调（风控 / 会话 / 外部平仓用 warn 提示，其余中性）。 */
export function explainKindTone(
  kind: TriggerExplain["kind"] | undefined,
): "long" | "short" | "neutral" | "warn" {
  if (!kind) return "neutral";
  return KIND_TONE[kind] ?? "neutral";
}

// 引擎风控出场原因 → 可读中文 headline（backtest-stop-loss-take-profit §三 #10）：止盈止损覆盖触发
// 强平时成交带 reason=stop_loss/take_profit，回落解释译为中文，避免成交表直显英文。
const RISK_REASON_HEADLINE: Record<string, string> = {
  stop_loss: "触发止损平仓",
  take_profit: "触发止盈平仓",
};

/** 据 legacy `reason` 推断类别（仅用于无 explain 的历史成交回落）。 */
function inferKindFromReason(trade: Trade): TriggerExplainKind {
  const reason = trade.reason || "";
  // 止损 / 止盈触发价强平与 max_hold 同属风控离场（warn 色调），统一归 risk_exit。
  if (reason === "max_hold" || reason === "stop_loss" || reason === "take_profit") return "risk_exit";
  // 利润保护四码（含 commitment 的 `profit_guard_committed_exit`，ai-trade-profit-protection §2.7 /
  // commitment §2.4）：`profit_guard_` 前缀**自动**把承诺退出线码归入「利润保护」kind，分类零改。
  if (reason.startsWith("profit_guard_")) return "profit_guard";
  if (reason === "session_stop") return "session_stop";
  if (reason === "external_close") return "external_close";
  if (reason === "reverse") return "reverse";
  if (trade.side === "close") return "atomic";
  return "atomic";
}

/** 取 explain；无则据 reason + 现有字段合成回落解释（展开始终有内容）。 */
export function resolveExplain(trade: Trade): TriggerExplain {
  if (trade.explain) return trade.explain;
  const kind = inferKindFromReason(trade);
  const metrics: TriggerExplain["metrics"] = [{ label: "成交价", value: fmtNum(trade.price) }];
  if (trade.side === "close") {
    metrics.push({
      label: "本笔盈亏",
      value: fmtSigned(trade.pnl),
      tone: trade.pnl > 0 ? "long" : trade.pnl < 0 ? "short" : "neutral",
    });
  }
  return {
    schema: 1,
    kind,
    side: trade.side,
    headline:
      RISK_REASON_HEADLINE[trade.reason || ""] ||
      trade.reason ||
      (trade.side === "close" ? "平仓" : "开仓"),
    summary: "该成交无结构化触发解释（历史成交或非自动交易），以下为可得信息回落。",
    metrics,
    risk: {
      stopLossPrice: trade.stopLossPrice ?? null,
      takeProfitPrice: trade.takeProfitPrice ?? null,
      stopLossPct: trade.stopLossPct ?? null,
      takeProfitPct: trade.takeProfitPct ?? null,
      maxHoldBars: trade.maxHoldBars ?? null,
    },
  };
}

export interface ContribBar {
  factorKey: string;
  name: string;
  pct: number; // 归一化宽度 ∈ [0,100]（最大 |贡献| 者为 100）
  signedPct: number; // 带符号宽度（diverging 零轴布局用）
  tone: "long" | "short";
  isTopDriver: boolean;
}

/**
 * 因子贡献条几何：按 |contribution| 降序，`pct = |c| / max|c| * 100`，`signedPct` 带符号；
 * `tone` 由贡献符号定（正→long、负→short）；`isTopDriver` 取前 3 且 share≥0.15（与后端一致）。
 */
export function contributionBars(factors: FactorContribution[] | undefined): ContribBar[] {
  if (!factors || factors.length === 0) return [];
  const ordered = factors
    .slice()
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
  const maxAbs = Math.max(...ordered.map((f) => Math.abs(f.contribution)), 0);
  return ordered.map((f, i) => {
    const pct = maxAbs > 0 ? (Math.abs(f.contribution) / maxAbs) * 100 : 0;
    const tone: "long" | "short" = f.contribution >= 0 ? "long" : "short";
    const isTopDriver =
      f.isTopDriver ?? (i < TOP_DRIVER_COUNT && (f.share ?? 0) >= TOP_DRIVER_SHARE);
    return {
      factorKey: f.factorKey,
      name: f.name,
      pct,
      signedPct: f.contribution >= 0 ? pct : -pct,
      tone,
      isTopDriver,
    };
  });
}

function fmtNum(value: number): string {
  return Number.isFinite(value) ? value.toFixed(2) : "—";
}

function fmtSigned(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)} USDT`;
}
