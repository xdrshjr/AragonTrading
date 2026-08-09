// 换手纪律与交易所侧兜底的前端纯格式化（ai-trade-turnover-budget-and-backstop §2.4.2）。
//
// **本文件零计算**：所有数字都由后端下发（`horizon.turnover` / `horizon.backstop`），这里只做
// 中文化与色调选择。两条不可让步的口径纪律：
//
// 1. **`null` 与 `0` 是两个结论**：`coverageRatio == null` 是「还不知道」、`0` 是「确认裸露」。
//    前者一律**灰色**，后者才是红色。把「不知道」渲染成确定的坏消息，比原来那句没核实的话更坏。
// 2. **「未核实」与「覆盖未知」都不用黄色**（沿用验收卡 D-6 的色彩纪律）：黄色会被读成「快好了」，
//    而这两态的语义都是「还不知道」。
import type { AiTradeBackstopWire, AiTradeTurnoverWire } from "./aiTradeTypes";
import { formatHoldDuration } from "./aiTradeHorizon";

export type TurnoverTone = "muted" | "accent" | "long" | "short";

export interface TurnoverRow {
  key: string;
  label: string;
  value: string;
  detail: string;
  tone: TurnoverTone;
}

const SIDE_LABEL: Record<string, string> = { long: "做多", short: "做空" };

/** 冷却行；未装配 ⇒ null ⇒ 整行不渲染（绝不在没有契约的会话上挂一排 0）。 */
export function describeCooldown(wire?: AiTradeTurnoverWire | null): TurnoverRow | null {
  if (!wire || !wire.cooldownSeconds) return null;
  if (!wire.cooldownActive) {
    return {
      key: "cooldown",
      label: "再入场冷却",
      value: "未激活",
      detail: `平仓后 ${formatHoldDuration(wire.cooldownSeconds)}内暂停同向开仓`,
      tone: "muted",
    };
  }
  const side = SIDE_LABEL[wire.cooldownSide ?? ""] ?? "同向";
  const scope =
    wire.cooldownScope === "both" ? "开仓（两个方向）暂停" : `${side}开仓暂停 · 反向不受限`;
  return {
    key: "cooldown",
    label: "再入场冷却",
    value: `冷却中 ${formatHoldDuration(wire.cooldownRemainingSeconds)}`,
    detail: scope,
    tone: "accent",
  };
}

/**
 * 换手预算行；未启用 ⇒ null。
 *
 * 措辞必须是「**本会话**每日最多 N 次」（[P1-5]）：令牌桶是会话内存态、启动即满桶，停会话重开
 * 即回满。本轮的全部论点都建立在「不许说没核实过的话」上，这一句必须自己先做到。
 */
export function describeBudget(wire?: AiTradeTurnoverWire | null): TurnoverRow | null {
  if (!wire?.budgetEnabled) return null;
  const tokens = wire.tokens ?? 0;
  const capacity = wire.capacity ?? 0;
  const exhausted = tokens < 1;
  const eta =
    wire.nextTokenSeconds == null
      ? ""
      : ` · 下一枚 ${formatHoldDuration(wire.nextTokenSeconds)}后到账`;
  const perDay = wire.perDay == null ? "—" : `${trim(wire.perDay)} 次/日`;
  return {
    key: "budget",
    label: "换手预算",
    value: `${tokens.toFixed(1)} / ${trim(capacity)}`,
    detail: `本会话上限 ${perDay}（重启后重新计算）${eta}`,
    tone: exhausted ? "short" : "muted",
  };
}

/** 令牌桶进度（0~1）供进度条渲染；未启用 ⇒ null（不画一根空条冒充「有预算」）。 */
export function budgetProgress(wire?: AiTradeTurnoverWire | null): number | null {
  if (!wire?.budgetEnabled) return null;
  const capacity = wire.capacity ?? 0;
  if (capacity <= 0) return null;
  return Math.max(0, Math.min(1, (wire.tokens ?? 0) / capacity));
}

/** 拦截计数行；两个都是 0 ⇒ null（一个健康的长周期会话本就可能一次都不撞闸，[P2-8]）。 */
export function describeVetoes(wire?: AiTradeTurnoverWire | null): TurnoverRow | null {
  if (!wire) return null;
  const total = (wire.reentryVetoes ?? 0) + (wire.budgetVetoes ?? 0);
  if (total <= 0) return null;
  return {
    key: "vetoes",
    label: "已拦下开仓意图",
    value: `${total} 次`,
    detail:
      `冷却 ${wire.reentryVetoes ?? 0} · 预算 ${wire.budgetVetoes ?? 0}` +
      "（模型仍在试图高频换手，本身就是一条需要看的读数）",
    tone: "muted",
  };
}

/**
 * 交易所侧兜底行；未装配 ⇒ null ⇒ 整块不渲染（历史页据此**不显示一个假的「已核实」绿勾**）。
 *
 * 五分支与后端 `_exchange_snapshot_line` 逐条镜像，色彩按 §2.4.2 的纪律：
 * `mismatch` 与「有数且覆盖不足」用红色（确定的坏消息）；`local` / `unavailable` /
 * `coverageRatio == null` 一律灰色（还不知道）。
 */
export function describeBackstop(wire?: AiTradeBackstopWire | null): TurnoverRow | null {
  if (!wire) return null;
  if (wire.basis !== "exchange") return unverifiedRow(wire);
  const ago = formatAgo(wire.checkedAgoSeconds);
  if (wire.mismatch) {
    return {
      key: "backstop",
      label: "交易所兜底",
      value: "核实为未挂",
      detail: `本地记录声称挂过止损，交易所侧一条都没有（${ago}核实）——此刻没有任何持久化兜底`,
      tone: "short",
    };
  }
  if (!wire.hasStop) {
    return {
      key: "backstop",
      label: "交易所兜底",
      value: "无止损委托",
      detail: `交易所侧现在没有任何止损委托（${ago}核实）——退出完全由本进程负责`,
      tone: "short",
    };
  }
  return coveredRow(wire, ago);
}

function coveredRow(wire: AiTradeBackstopWire, ago: string): TurnoverRow {
  const px = wire.stopLossPx == null ? "—" : trim(wire.stopLossPx);
  // `null` 是一等公民：交易所确认有一张止损、但没回报可核对的张数 ⇒ 灰色的「覆盖未知」。
  if (wire.coverageRatio == null) {
    return {
      key: "backstop",
      label: "交易所兜底",
      value: "已核实 · 覆盖未知",
      detail: `止损 ${px}（${ago}核实）——交易所未回报可核对的委托张数，无法确认是否覆盖全部持仓`,
      tone: "muted",
    };
  }
  const pct = `${Math.round(wire.coverageRatio * 100)}%`;
  if (wire.coverageRatio < 0.999) {
    return {
      key: "backstop",
      label: "交易所兜底",
      value: `覆盖 ${pct}`,
      detail: `止损 ${px}（${ago}核实）——未覆盖部分在本进程退出后没有兜底`,
      tone: "short",
    };
  }
  return {
    key: "backstop",
    label: "交易所兜底",
    value: `已核实 · 覆盖 ${pct}`,
    detail: `止损 ${px}（${ago}核实，进程退出后仍生效）`,
    tone: "long",
  };
}

const UNVERIFIED_REASON: Record<string, string> = {
  disabled: "核实通道未开启",
  no_creds: "未接入交易所凭证",
  not_run: "本会话尚未跑过一次核实",
  error: "最近一次核实失败",
  stale: "最近一次核实已过期",
};

function unverifiedRow(wire: AiTradeBackstopWire): TurnoverRow {
  const reason = UNVERIFIED_REASON[wire.unavailableReason ?? ""] ?? "尚未核实";
  const local =
    wire.localStopLossPx == null ? "本地也未记录止损" : `本地回执止损 ${trim(wire.localStopLossPx)}`;
  return {
    key: "backstop",
    label: "交易所兜底",
    value: "未核实",
    detail: `${reason}；${local}——不能据此假定进程退出后仍生效`,
    tone: "muted",
  };
}

/**
 * 卡片排序键（§2.4.2「最紧要优先」）：数字越小越靠前。
 * `mismatch` > 覆盖不足 > 冷却中 > 预算耗尽 > 正常。
 */
export function turnoverSeverity(
  turnover?: AiTradeTurnoverWire | null,
  backstop?: AiTradeBackstopWire | null,
): number {
  if (backstop?.basis === "exchange" && backstop.mismatch) return 0;
  if (
    backstop?.basis === "exchange" &&
    backstop.coverageRatio != null &&
    backstop.coverageRatio < 0.999
  ) {
    return 1;
  }
  if (turnover?.cooldownActive) return 2;
  if (turnover?.budgetEnabled && (turnover.tokens ?? 0) < 1) return 3;
  return 4;
}

/** 面板 chip：只显示**最紧要的一格**；正常态返回 null ⇒ 不占位（R-7 信息过载）。 */
export function describeTurnoverChip(
  turnover?: AiTradeTurnoverWire | null,
  backstop?: AiTradeBackstopWire | null,
): { label: string; tone: TurnoverTone } | null {
  const severity = turnoverSeverity(turnover, backstop);
  if (severity === 0) return { label: "交易所兜底：核实为未挂", tone: "short" };
  if (severity === 1) {
    return {
      label: `交易所兜底覆盖 ${Math.round((backstop?.coverageRatio ?? 0) * 100)}%`,
      tone: "short",
    };
  }
  if (severity === 2) {
    const side = SIDE_LABEL[turnover?.cooldownSide ?? ""] ?? "同向";
    return {
      label: `再入场冷却 ${formatHoldDuration(turnover?.cooldownRemainingSeconds)}（${side}）`,
      tone: "accent",
    };
  }
  if (severity === 3) {
    const eta =
      turnover?.nextTokenSeconds == null
        ? ""
        : `，下一枚 ${formatHoldDuration(turnover.nextTokenSeconds)}后`;
    return { label: `换手预算已用尽${eta}`, tone: "short" };
  }
  return null;
}

/** 「多久之前」——秒级也要说得出口（与后端 `_fmt_ago` 同口径）。 */
export function formatAgo(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "刚刚";
  if (seconds < 60) return `${Math.round(seconds)}s 前`;
  return `${formatHoldDuration(seconds)}前`;
}

function trim(value: number): string {
  return String(Math.round(value * 1e6) / 1e6);
}
