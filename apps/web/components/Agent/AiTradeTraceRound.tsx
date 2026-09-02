"use client";

import { useState } from "react";
import {
  type AiTraceTone,
  describeAiAction,
  describeBaseRule,
  describeConsensus,
  describeDegradeReason,
  describeEdgeGate,
  describeExecuted,
  describeFactors,
  describeCandidateLevel,
  describeMultiTimeframeAlignment,
  describeCommittedExit,
  describeMultiTimeframeFrame,
  describeOrderType,
  describePriceAction,
  describeProfitGuardCooldown,
  describeProfitGuardFallback,
  describeProfitGuardStatus,
  describeProfitGuardTrail,
  describeReflection,
  describeRegime,
  describeSizing,
  describeStance,
  isEffectiveExitCommitted,
  describeTacticalAlignment,
  describeTacticalFrame,
  describeTraceState,
  describeTrigger,
  describeVolume,
  describeWakeReason,
} from "@/lib/aiTrade";
import { describeHorizonGate, formatHoldDuration } from "@/lib/aiTradeHorizon";
import {
  describeTrendAlignment,
  describeTrendGateAlignment,
  describeTrendVerdict,
  missingLabels,
  trendVerdictTone,
} from "@/lib/aiTradeTrend";
import AiTradeReasoningBlock, { ReasoningStageView } from "./AiTradeReasoningBlock";
import { ExitPriorityRow, ProposedVetoRow } from "./AiTradeExitPriorityRows";
import type {
  AiTradeRoundTrace,
  AiTradeTrendGate,
  AiTradeTrendVerdict,
  MultiTimeframeContext,
  TacticalContext,
} from "@/lib/aiTradeTypes";
import { fmtNum, fmtPrice, fmtTime } from "@/lib/format";

const TONE_VAR: Record<AiTraceTone, string> = {
  long: "var(--long)",
  short: "var(--short)",
  muted: "var(--muted)",
  accent: "var(--accent)",
};

function toneSoft(tone: AiTraceTone): string {
  return `color-mix(in srgb, ${TONE_VAR[tone]} 14%, transparent)`;
}

/** 轮头：时间（主）· 触发原因（着色）· 最新价（次要）+ 右侧状态 chip（deciding 附脉冲点）。 */
function RoundHeader({ trace }: { trace: AiTradeRoundTrace }) {
  const chip = describeTraceState(trace);
  const trigger = describeTrigger(trace);
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-[var(--muted)]">
        <span className="tabular shrink-0 font-medium text-[var(--text)]">{fmtTime(trace.ts, false)}</span>
        <span className="min-w-0 truncate" style={{ color: TONE_VAR[trigger.tone] }}>
          {trigger.label}
        </span>
        {trace.lastPx != null && <span className="tabular shrink-0">· {fmtPrice(trace.lastPx)}</span>}
      </span>
      <span
        className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
        style={{ color: TONE_VAR[chip.tone], backgroundColor: toneSoft(chip.tone) }}
      >
        {chip.tone === "accent" && (
          <span
            className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
            style={{ backgroundColor: TONE_VAR[chip.tone] }}
          />
        )}
        {chip.label}
      </span>
    </div>
  );
}

/**
 * 重试面包屑（ai-trade-decision-llm-retry §4.4/§五）：本轮发生过重试时如实呈现进度 + 上次触发原因。
 * 研判中（deciding）显示「⚠ 重试中 · 第 n 次…」并附脉冲点；已结算显示「↻ 本轮重试 n 次」。
 * 注：round trace 仅持久化 retries/lastRetryReason（§七），故以已重试次数 n 呈现（不含 m）。
 */
function RetryBreadcrumb({ trace }: { trace: AiTradeRoundTrace }) {
  const retries = trace.retries ?? 0;
  if (retries <= 0) return null;
  const active = trace.state === "deciding";
  const reason = trace.lastRetryReason;
  return (
    <p
      className="flex items-center gap-1 text-[10px]"
      style={{ color: active ? "var(--accent-ink)" : "var(--muted)" }}
    >
      <span>{active ? "⚠" : "↻"}</span>
      <span>
        {active ? `重试中 · 第 ${retries} 次…` : `本轮重试 ${retries} 次`}
        {reason ? `（${reason}）` : ""}
      </span>
      {active && (
        <span className="inline-block h-1 w-1 animate-pulse rounded-full bg-[var(--accent)]" />
      )}
    </p>
  );
}

/**
 * 趋势基调·逆势门槛的判定行（execution-trend-verdict §3.7.4-3 / §5.3）。
 *
 * 后端**只在本层真的判过**（`lane != "exempt"`）的轮次下发这个键，故它一出现就必然有话要说：
 * 顺势直通、逆势放行（连同被强制降到多少）、还是逆势被拦（模型自报的置信度差在哪里）。
 * 少了这一行，`trendGate` 就是一份「后端算了、类型补了、没有渲染落点」的死数据——逆势**放行并
 * 被 ×0.5 降档**那一档在界面上会完全不可见（被拦的那一档尚可从降级码文案读到）。
 *
 * **两个 confidence 必须各有其名**：这里显示的是 `modelConfidence`（模型本轮自报、是本层唯一
 * 的判据）；`verdictConfidence` 是基调自身的置信度、纯呈现，取错方向正好相反，故不并进同一句。
 */
function TrendGateRow({ gate }: { gate: AiTradeTrendGate }) {
  const score =
    gate.execScore == null ? "执行周期分不可用" : `执行周期分 ${gate.execScore.toFixed(2)}`;
  let detail: string;
  if (gate.lane === "veto") {
    const model = gate.modelConfidence == null
      ? "不可用"
      : `${fmtNum(gate.modelConfidence * 100, 0)}%`;
    detail = `模型自报置信 ${model} · 门槛 ${fmtNum(gate.minConfidence * 100, 0)}% ⇒ 本轮观望`;
  } else if (gate.sizeBefore != null && gate.sizeAfter != null) {
    detail =
      `仓位 ${fmtNum(gate.sizeBefore * 100, 0)}% → ${fmtNum(gate.sizeAfter * 100, 0)}%` +
      `（强制降档 ×${gate.sizeMult}）`;
  } else {
    detail = "本层未收紧本轮仓位";
  }
  return (
    <p
      className="border-t border-[var(--border)] px-2.5 py-1.5 text-[11px] leading-relaxed text-[var(--muted)]"
      data-testid="trend-gate-row"
    >
      趋势基调·逆势门槛：{describeTrendGateAlignment(gate.alignment)}（{score}）· {detail}
    </p>
  );
}

/** 决策工具卡：动作 + 仓位/置信 + reason，可展开原始 input JSON；pending 时占位。 */
function DecisionToolCard({ trace }: { trace: AiTradeRoundTrace }) {
  const [open, setOpen] = useState(false);
  const call = trace.toolCall;
  if (!call) {
    if (!trace.toolCallPending) return null;
    return (
      <div className="flex items-center gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-[11px] text-[var(--muted)]">
        <span className="text-sm leading-none">🛠️</span>
        <span>调用决策工具…</span>
        <span className="ml-auto inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
      </div>
    );
  }
  const isOpen = call.action === "open_long" || call.action === "open_short";
  // 委托方式仅对 open_* / close 有意义（hold / cancel_order 不下单，§2.2），故其余动作不展示。
  const isOrder = isOpen || call.action === "close";
  const orderType = describeOrderType(call.orderType);
  const orderText =
    call.orderType === "limit" && call.limitPrice != null
      ? `${orderType} @ ${fmtPrice(call.limitPrice)}`
      : orderType;
  return (
    <div className="overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)] text-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
      >
        <span className="text-sm leading-none">🛠️</span>
        <span className="font-medium text-[var(--text)]">决策工具 · {describeAiAction(call.action)}</span>
        {isOrder && (
          <span className="tabular shrink-0 text-[10px] text-[var(--muted)]">{orderText}</span>
        )}
        <span className="ml-auto tabular text-[10px] text-[var(--muted)]">
          {isOpen && call.sizeFraction != null ? `仓位 ${fmtNum(call.sizeFraction * 100, 0)}% · ` : ""}
          {call.confidence != null ? `置信 ${fmtNum(call.confidence * 100, 0)}%` : ""}
        </span>
        <span className="text-[var(--muted)]">{open ? "▾" : "▸"}</span>
      </button>
      <ProposedVetoRow trace={trace} />
      {trace.trendGate ? <TrendGateRow gate={trace.trendGate} /> : null}
      {call.reason && (
        <p className="border-t border-[var(--border)] px-2.5 py-1.5 text-[11px] leading-relaxed text-[var(--muted)]">
          {call.reason}
        </p>
      )}
      {open && (
        <pre className="overflow-x-auto whitespace-pre-wrap break-all border-t border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-2 text-[10px] leading-relaxed text-[var(--muted)]">
          {JSON.stringify(call, null, 2)}
        </pre>
      )}
    </div>
  );
}

/**
 * 【基础策略】铁律段（ai-trade-base-strategy-ma-band §2.9）：置于决策工具卡与二次反思之间——铁律先于
 * 反思执行，卡片顺序即管线顺序。三态徽标 + 通道断面（上/中/下轨 + %B）；否决轮另给一行人话理由。
 * 规则未装配的会话 `baseRuleStatus` 恒为空 ⇒ 整段不渲染（老会话与老快照零影响）。
 */
function BaseRuleBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const view = describeBaseRule(trace);
  if (!view) return null;
  const hasBand = trace.baseRulePctB != null;
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_6%,transparent)] px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[11px] font-medium text-[var(--text)]">
          <span className="leading-none">📏</span>基础策略（MA 通道开单铁律）
        </span>
        <span
          className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
          style={{ color: TONE_VAR[view.tone], backgroundColor: toneSoft(view.tone) }}
        >
          {view.label}
        </span>
      </div>
      {hasBand ? (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">
          通道内位置 %B={fmtNum(trace.baseRulePctB, 2)}
          {trace.baseRuleZone ? "（0=下轨 / 0.5=中轨 / 1=上轨）" : " · 不在任何锚区附近"}
        </p>
      ) : (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">
          通道数据不足，本轮开仓一律降级观望；平仓与撤单不受影响。
        </p>
      )}
      {trace.baseRuleReason && (
        // P1-9：降级码经 describeDegradeReason 中文化后再展示（与 describeExecuted 同一真源），
        // 否则用户在此看到的是裸 `base_rule_ma_band(pctB=0.42)`，与本段「人话理由」的职责相悖。
        <p className="text-[11px] leading-relaxed text-[var(--text)]">
          {describeDegradeReason(trace.baseRuleReason)}
        </p>
      )}
    </div>
  );
}

/**
 * 交易周期铁律段（ai-trade-holding-horizon-modes §2.11）：置于基础策略之后——管线顺序即卡片顺序
 * （铁律 → **本层** → 冷却 → 刹车 → 共识 → 成本门禁 → 反思）。
 *
 * 徽标与铁律段同排同款，但**极性相反**：本层 fail-open，「限制已解除」意味着放行而非拦截，故用
 * accent 而非红色。契约未装配 ⇒ `horizonStatus` 恒为空 ⇒ 整段不渲染（老会话与老快照零影响）。
 *
 * 三行读数分别对应三道闸——**必须同时摆出来**：只显示「被拦了」而不说是哪道闸、还差多久，用户
 * 无从判断该等还是该关掉本层（本仓库最忌讳的不可解释保守）。
 */
function HorizonBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const view = describeHorizonGate(trace.horizonStatus, trace.horizonWire);
  if (!view) return null;
  const wire = trace.horizonWire;
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_6%,transparent)] px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[11px] font-medium text-[var(--text)]">
          <span className="leading-none">⏳</span>交易周期（持仓时长铁律）
        </span>
        <span
          className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
          style={{ color: TONE_VAR[view.tone], backgroundColor: toneSoft(view.tone) }}
        >
          {view.label}
        </span>
      </div>
      <ExitPriorityRow trace={trace} />
      {wire && wire.status !== "flat" ? (
        <HorizonGateRows wire={wire} />
      ) : (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">
          当前空仓：本层<strong className="text-[var(--text)]">不约束开仓</strong>；开仓后才按目标
          周期约束 AI 主动平仓与反手。
        </p>
      )}
      {trace.horizonReason && (
        <p className="text-[11px] leading-relaxed text-[var(--text)]">
          {describeDegradeReason(trace.horizonReason)}
        </p>
      )}
    </div>
  );
}

/** 三道闸的逐行读数（空转的闸整段省略——写「本闸未启用」只是噪音，与 base_rule 段同款降级范式）。 */
function HorizonGateRows({ wire }: { wire: NonNullable<AiTradeRoundTrace["horizonWire"]> }) {
  const rows: string[] = [];
  const held = formatHoldDuration(wire.holdSeconds);
  rows.push(
    wire.minHoldSeconds > 0
      ? `已持仓 ${held} / 最短 ${formatHoldDuration(wire.minHoldSeconds)}` +
          (wire.minHoldRemainingSeconds > 0
            ? `（还差 ${formatHoldDuration(wire.minHoldRemainingSeconds)}）`
            : "（已满足）")
      : `已持仓 ${held}（本会话未设最短持仓窗口）`,
  );
  if (wire.exitConfirmRounds > 1) {
    const window = wire.confirmWindowElapsed
      ? "确认窗口已到，按意愿放行"
      : wire.confirmWindowRemainingSeconds != null
        ? `窗口剩余 ${formatHoldDuration(wire.confirmWindowRemainingSeconds)}`
        : "尚未提出离场";
    rows.push(`离场确认 ${wire.exitConfirmStreak}/${wire.exitConfirmRounds}（${window}）`);
  }
  if (wire.requiredMoveBps != null) {
    const move = wire.moveBps == null ? "—" : `${wire.moveBps.toFixed(1)}bps`;
    rows.push(`换手迟滞：需 ≥${wire.requiredMoveBps.toFixed(1)}bps，当前 ${move}`);
  }
  return (
    <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-horizon-rows">
      {rows.join(" ｜ ")}
    </p>
  );
}

/** 说明文案按分子来源分档：接入先验后写死的「min(ATR 预算, 止盈距离)」会说谎（signal-prior §6.3）。 */
const EDGE_SOURCE_NOTE: Record<string, string> = {
  unavailable:
    "本轮无法估算期望收益（ATR 数据不足），开仓一律放行；本层只会更保守，不会因估不出而拦单。",
  atr: "粗估毛收益 = ATR 波动预算（一个「若走对能吃到多少」的幅度，不含胜率）；成本 = 双边手续费 + 穿越价差 + 冲击 + 资金费率。",
  take_profit:
    "粗估毛收益 = 单仓止盈距离（模型再有想法也拿不到超过 OCO 止盈的利润）；成本 = 双边手续费 + 穿越价差 + 冲击 + 资金费率。",
  prior:
    "毛收益 = 信号先验期望 p·W −(1−p)·L（历史统计，非当下保证；已按胜率折算，故数量级远小于 ATR 幅度）；成本 = 双边手续费 + 穿越价差 + 冲击 + 资金费率。",
};

/**
 * 最小 edge 门禁段（ai-trade-cost-accounting-and-edge-gate §4.3）：与铁律段并列、紧随其后——管线顺序
 * 即卡片顺序（铁律 → 冷却 → 共识 → **本层** → 反思）。徽标与铁律段同排同款，但**极性相反**：本层
 * fail-open，「无法估算」意味着放行而非拦截，故用 accent 而非红色，文案也把这件事讲清楚。
 * 门禁未启用（`k_min=0`，默认）⇒ `edgeGateStatus` 恒为空 ⇒ 整段不渲染（老会话与老快照零影响）。
 * 说明文案按 `edgeSource` 取 `EDGE_SOURCE_NOTE`（signal-prior-and-ev-gate §6.3）。
 */
function EdgeGateBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const view = describeEdgeGate(trace);
  if (!view) return null;
  const unavailable = trace.edgeGateStatus === "unavailable";
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_6%,transparent)] px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[11px] font-medium text-[var(--text)]">
          <span className="leading-none">⚖️</span>成本门禁（期望收益 vs 往返成本）
        </span>
        <span
          className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
          style={{ color: TONE_VAR[view.tone], backgroundColor: toneSoft(view.tone) }}
        >
          {view.label}
        </span>
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        {EDGE_SOURCE_NOTE[unavailable ? "unavailable" : (trace.edgeSource ?? "atr")] ??
          EDGE_SOURCE_NOTE.atr}
      </p>
      {trace.edgeGateReason && (
        <p className="text-[11px] leading-relaxed text-[var(--text)]">
          {describeDegradeReason(trace.edgeGateReason)}
        </p>
      )}
    </div>
  );
}

/**
 * 仓位与风险段（vol-sizing-and-drawdown-brake §6.3 第 7a 点）：紧随成本门禁之后——管线顺序即卡片顺序
 * （铁律 → 刹车 → 共识 → 成本门禁 → 反思 → **本层**）。三条规则全未装配 ⇒ `describeSizing` 返回 null
 * ⇒ 整段不渲染（老会话与老快照零影响）。
 *
 * 刻意把 `fFinalRaw` 与 `finalFraction` **同时**摆出来：两者之差就是被档位地板吃掉的量。只显示后者会让
 * 一次纯 no-op 的减速看起来和真减速一模一样，那正是本仓库最忌讳的假阳性观测。
 */
function SizingBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const view = describeSizing(trace);
  if (!view) return null;
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_6%,transparent)] px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[11px] font-medium text-[var(--text)]">
          <span className="leading-none">📐</span>仓位与风险（下多大注 / 止损多远）
        </span>
        <span
          className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
          style={{ color: TONE_VAR[view.tone], backgroundColor: toneSoft(view.tone) }}
        >
          {view.label}
        </span>
      </div>
      {view.note && <p className="text-[10px] leading-relaxed text-[var(--muted)]">{view.note}</p>}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-[var(--muted)]">
        {trace.fVol != null && <span>波动率目标项 {fmtNum(trace.fVol, 2)}</span>}
        {trace.fKelly != null && <span>凯利项 {fmtNum(trace.fKelly, 2)}</span>}
        {trace.fFinalRaw != null && <span>吸附前 {fmtNum(trace.fFinalRaw, 3)}</span>}
        {trace.finalFraction != null && <span>实际下单占比 {fmtNum(trace.finalFraction, 2)}</span>}
        {trace.stopPct != null && trace.takePct != null && (
          <span>
            止损 ±{fmtNum(trace.stopPct * 100, 2)}% / 止盈 {fmtNum(trace.takePct * 100, 2)}%
          </span>
        )}
        {trace.drawdownPct != null && <span>会话回撤 {fmtNum(trace.drawdownPct * 100, 1)}%（含未实现）</span>}
        {trace.corrScale != null && trace.corrScale < 1 && <span>相关性缩减 ×{fmtNum(trace.corrScale, 2)}</span>}
      </div>
      {trace.brakeReason && (
        <p className="text-[11px] leading-relaxed text-[var(--text)]">
          {describeDegradeReason(trace.brakeReason)}
        </p>
      )}
    </div>
  );
}

/**
 * 利润保护段（ai-trade-profit-protection §2.7）：与基础策略段并列，置于其后——两者都是「系统强制执行」
 * 的层，但一个管「在哪能开」、一个管「利润何时被强制锁定」。快照缺失（规则未装配 / 老快照）⇒ 整段不渲染。
 *
 * `unavailable` 只出一句「暂不可用」，**绝不显示退出线**：一条算得出来却不作数的价位比没有更危险。
 *
 * `status="flat"` 的空壳快照（`describeProfitGuardStatus` 对该态恒返回 null）**仍须渲染**：它是
 * `lastExit` 回执唯一的载体，而回执又只在保护平仓后的那一轮出现（P1-7 读取即消费）——彼时仓位已平、
 * 状态恰恰就是 flat。只按 `view` 早退会把「上一笔仓位为何消失」这条回执在 UI 上悄悄吞掉。
 */
function ProfitGuardBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const guard = trace.profitGuard ?? null;
  const view = describeProfitGuardStatus(guard);
  const cooldown = describeProfitGuardCooldown(trace.profitGuardCooldown);
  // 冷却轮里持仓已平（guard 多为 flat 空壳甚至缺席），但「本轮为什么被降级为观望」恰恰要在这里说清楚
  // ——否则用户只看到一个没有理由的 hold。故冷却单独构成一条渲染条件。
  if (!guard && !cooldown) return null;
  if (guard && !view && !guard.lastExit && !cooldown) return null;
  const unavailable = guard?.status === "unavailable";
  const trail = describeProfitGuardTrail(guard);
  // 承诺退出线（commitment C4）：模型自己许下的第三条保护线；「当前由它定住」只读后端归因（[P1-3]）。
  const committed = describeCommittedExit(guard);
  const lastExit = guard?.lastExit ?? null;
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_6%,transparent)] px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[11px] font-medium text-[var(--text)]">
          <span className="leading-none">🛡️</span>利润保护（移动止盈 / 保本）
        </span>
        {view && (
          <span
            className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
            style={{ color: TONE_VAR[view.tone], backgroundColor: toneSoft(view.tone) }}
          >
            {view.label}
          </span>
        )}
      </div>
      {guard &&
        view &&
        (unavailable ? (
          <p className="text-[10px] leading-relaxed text-[var(--muted)]">
            {describeProfitGuardFallback(guard)}。
          </p>
        ) : (
          <p className="tabular text-[10px] leading-relaxed text-[var(--muted)]">
            峰值浮盈 {guard.peakUnrealizedPnlUsd == null ? "—" : fmtNum(guard.peakUnrealizedPnlUsd, 3)} USDT
            {" · 已回吐 "}
            {guard.givebackRatio == null ? "—" : `${(guard.givebackRatio * 100).toFixed(1)}%`}
            {" · 有效退出线 "}
            {guard.effectiveExitPx == null ? "未武装" : fmtPrice(guard.effectiveExitPx)}
            {trail && <span className="block">{trail}</span>}
            {committed && (
              <span className="block" data-testid="trace-profit-committed">
                {committed}
                {isEffectiveExitCommitted(guard) ? "（当前由它定住）" : ""}
              </span>
            )}
          </p>
        ))}
      {lastExit && (
        <p className="text-[11px] leading-relaxed text-[var(--text)]">
          上一笔持仓已由利润保护层强制平仓（{lastExit.label ?? "利润保护"}）
          {lastExit.exitPx == null ? "" : ` @ ${fmtPrice(lastExit.exitPx)}`}
        </p>
      )}
      {cooldown && (
        <p className="text-[10px] leading-relaxed text-[var(--accent-ink)]" data-testid="trace-profit-cooldown">
          {cooldown}
        </p>
      )}
    </div>
  );
}

/**
 * 二次反思（ReAct 校验）段（ai-trade-pre-trade-reflection §三）：置于决策工具卡与执行结果之间——
 * 流式反思复核文本（markdown）+ 裁决 chip + 理由 + 收缩后仓位。仅交易意愿轮（有反思在飞/文本/裁决）渲染。
 */
function ReflectionBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const view = describeReflection(trace);
  const text = trace.reflection ?? "";
  const streaming = Boolean(trace.reflectionPending);
  if (!view && !text) return null; // hold / 关闭反思轮：不渲染反思段
  const adjusted = trace.reflectionAdjustedSize;
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-[var(--accent-soft)] bg-[color-mix(in_srgb,var(--accent)_6%,transparent)] px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[11px] font-medium text-[var(--text)]">
          <span className="leading-none">🔎</span>二次反思（ReAct 校验）
        </span>
        {view && (
          <span
            className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
            style={{ color: TONE_VAR[view.tone], backgroundColor: toneSoft(view.tone) }}
          >
            {streaming && (
              <span
                className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
                style={{ backgroundColor: TONE_VAR[view.tone] }}
              />
            )}
            {view.label}
          </span>
        )}
      </div>
      {(text || streaming) && (
        <div className="text-[11px] leading-relaxed text-[var(--muted)]">
          {!text ? (
            <span>正在复核…</span>
          ) : (
            // 复核段与研判段**视觉同源、解析同源**（`kind="review"` ⇒ 7 格导轨、R6/R7 默认展开）；
            // 未按协议输出阶段标记时优雅回退到整段渲染（与合入前逐字一致）。
            <ReasoningStageView text={text} kind="review" streaming={streaming} />
          )}
        </div>
      )}
      {trace.reflectionReason && !streaming && (
        <p className="text-[11px] leading-relaxed text-[var(--text)]">{trace.reflectionReason}</p>
      )}
      {adjusted != null && (
        <p className="text-[10px] text-[var(--muted)]">
          收缩后仓位 {fmtNum(adjusted * 100, 0)}%
        </p>
      )}
    </div>
  );
}

/** 执行结果行：按 AiExecTone 着色的状态点 + 中文标签 + 结果持仓方向；异常轮显示 errorMessage。 */
function ResultRow({ trace }: { trace: AiTradeRoundTrace }) {
  if (trace.errorMessage) {
    return (
      <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[11px] text-[var(--short-ink)]">
        ⚠️ {trace.errorMessage}
      </p>
    );
  }
  if (!trace.executed) return null;
  const action = trace.toolCall?.action ?? "hold";
  const exec = describeExecuted(action, trace.executed, trace.degradeReason);
  const posAfter = trace.executed.positionAfter;
  return (
    <div className="flex items-center gap-1.5 text-[11px]" style={{ color: TONE_VAR[exec.tone] }}>
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: TONE_VAR[exec.tone] }}
      />
      <span className="font-medium">{exec.label}</span>
      {posAfter && (
        <span className="text-[var(--muted)]">· 结果持仓 {describeStance(posAfter)}</span>
      )}
    </div>
  );
}

const MTF_ORDER = new Map(["1D", "12H", "4H", "1H"].map((bar, index) => [bar, index]));

/**
 * 本轮趋势基调（execution-trend-verdict §3.7.4-7）。
 *
 * 渲染在 `MultiTimeframeEvidence` **之前**——阅读顺序即证据顺序：用户诉求的第一步是
 * 「先判我所选的这个级别是涨是跌」，中线背景只是对它的佐证。
 *
 * 两条呈现纪律：
 * - **两个分分开写**：`execScore` 决定强度（只由执行周期证据算出）、`score` 决定方向（含高
 *   周期调制）。合并成一个数，用户就无法回答「这个强度里有多少是日线给的」。
 * - **缺失成分如实列出**，绝不补猜；降级轮不出现任何方向词。
 */
function TrendVerdictEvidence({ verdict }: { verdict: AiTradeTrendVerdict }) {
  const tone = trendVerdictTone(verdict);
  const missing = missingLabels(verdict);
  return (
    <div className="flex flex-col gap-0.5" data-testid="trend-verdict-evidence">
      <div className="font-medium" style={{ color: TONE_VAR[tone] }}>
        {describeTrendVerdict(verdict)}
      </div>
      {!verdict.degraded && (
        <>
          <div className="flex flex-wrap gap-x-2 gap-y-0.5">
            {verdict.components.map((component) => (
              <span key={component.key} data-testid="trend-component">
                {component.label}{" "}
                {component.score == null ? "不可用" : component.score.toFixed(2)}
                <span className="opacity-60">（权重 {component.weight.toFixed(2)}）</span>
              </span>
            ))}
          </div>
          <div>{describeTrendAlignment(verdict)}</div>
        </>
      )}
      {missing.length > 0 && <div>成分缺失：{missing.join("、")}（不得补猜）</div>}
    </div>
  );
}

function MultiTimeframeEvidence({ context }: { context: MultiTimeframeContext }) {
  if (!context.frames.length) {
    return <div>中线背景暂不可用，已回退基础周期</div>;
  }
  const frames = [...context.frames].sort(
    (left, right) => (MTF_ORDER.get(left.bar) ?? 99) - (MTF_ORDER.get(right.bar) ?? 99),
  );
  return (
    <div className="flex flex-col gap-0.5">
      <div className="font-medium text-[var(--text)]">
        中线背景 · {describeMultiTimeframeAlignment(context.trendAlignment.status)}
      </div>
      <div>候选支撑：{describeCandidateLevel(context.nearestSupport)}</div>
      <div>候选阻力：{describeCandidateLevel(context.nearestResistance)}</div>
      {frames.map((frame) => (
        <div key={frame.bar} data-testid="mtf-frame">
          {describeMultiTimeframeFrame(frame)}
        </div>
      ))}
      {context.missingBars.length ? <div>缺失周期：{context.missingBars.join("、")}</div> : null}
    </div>
  );
}

/**
 * 战术周期证据（wake-aware-context §3.3）：与中线背景块视觉同构（同字号、同行距、同色阶），
 * 只是时间尺度相反——中线由大到小讲背景，战术由粗到细讲「现在是不是好的进场点」。
 * 缺失 / 陈旧周期如实标注，绝不假数据。
 */
function TacticalEvidence({ context }: { context: TacticalContext }) {
  const change = context.changeSinceLastRound;
  return (
    <div className="flex flex-col gap-0.5">
      <div className="font-medium text-[var(--text)]">
        战术周期 · {describeTacticalAlignment(context.alignment)}
      </div>
      {change?.pct != null && (
        <div>
          自上一决策轮：价 {change.pct > 0 ? "+" : ""}
          {change.pct.toFixed(2)}%
          {change.atrMult == null ? "" : `（≈${change.atrMult.toFixed(2)}×${change.atrBar} ATR）`}
        </div>
      )}
      {context.frames.map((frame) => (
        <div key={frame.bar} data-testid="tactical-frame">
          {describeTacticalFrame(frame)}
        </div>
      ))}
      {context.missingBars.length ? <div>缺失周期：{context.missingBars.join("、")}</div> : null}
    </div>
  );
}

/**
 * 补跑证据（轴 A）：上一轮研判期间丢了什么、最早的一次等了多久。
 *
 * 「等待时长」的口径是**自首次丢弃至今**，不是上一轮的研判耗时——后端没有把轮耗时记进 backlog，
 * 写成后者就是在界面上给出一个用户无从校验的错数。
 */
function WakeBacklogEvidence({
  backlog,
}: {
  backlog: NonNullable<AiTradeRoundTrace["wake"]>["backlog"];
}) {
  const bars = backlog?.droppedBars ?? 0;
  const intervals = backlog?.droppedIntervals ?? 0;
  if (!bars && !intervals) return null;
  const parts = [
    bars ? `${bars} 根 K 线收线未被研判` : "",
    intervals ? `${intervals} 次定时唤醒被丢弃` : "",
  ].filter(Boolean);
  const waited = backlog?.waitedS;
  return (
    <div data-testid="wake-backlog" className="text-[var(--accent-ink)]">
      补跑 · 上一轮研判期间{parts.join("、")}
      {typeof waited === "number" ? `，最早的一次已等待 ${Math.round(waited)}s` : ""}
      {backlog?.chain ? `（补跑链第 ${backlog.chain} 轮）` : ""}
    </div>
  );
}

/**
 * 立场翻转证据（轴 C）：**未收线**的预演结论，必须如实标注 provisional / repaint。
 *
 * 信号板与全部门禁仍以最近确认根为准——UI 把它呈现成既成事实，就等于用界面撤销了那条安全隔离。
 */
function WakeFlipEvidence({ flip }: { flip: NonNullable<AiTradeRoundTrace["wake"]>["flip"] }) {
  if (!flip) return null;
  const names = (flip.strategies ?? [])
    .slice(0, 3)
    .map((s) => `${s.name ?? "?"}（${describeStance(s.from ?? "")}→${describeStance(s.to ?? "")}）`)
    .join("、");
  return (
    <div data-testid="wake-flip" className="flex flex-col gap-0.5">
      <div className="font-medium text-[var(--text)]">
        立场翻转 · {describeStance(flip.netStanceFrom ?? "")} →{" "}
        {describeStance(flip.netStanceTo ?? "")}
        {describeFlipEvidenceStrength(flip)}
      </div>
      {names ? <div>{names}</div> : null}
      <div>未收线预演结果，可能随收线反复（repaint）；信号板与全部门禁仍以最近确认根为准。</div>
    </div>
  );
}

// 防抖强度小句：「已保持 47s / 4 次有效观测」。两项各自可缺（老事件 / 部分下发），缺项即省略，
// 绝不出现半个括号或一个凭空的 0——「有效观测次数」是这条证据成立与否的核心论据，不许含糊。
function describeFlipEvidenceStrength(
  flip: NonNullable<NonNullable<AiTradeRoundTrace["wake"]>["flip"]>,
): string {
  const parts: string[] = [];
  if (typeof flip.heldS === "number") parts.push(`已保持 ${Math.round(flip.heldS)}s`);
  if (typeof flip.samples === "number") parts.push(`${flip.samples} 次有效观测`);
  return parts.length ? `（${parts.join(" / ")}）` : "";
}

/** 输入信号板 / 中线背景 / 战术周期 / 体制 / 共识 / 市场证据摘要。 */
function SignalContext({ trace }: { trace: AiTradeRoundTrace }) {
  const regime = describeRegime(trace.marketContext);
  const consensus = describeConsensus(trace.consensus ?? null);
  const digest = trace.marketDigest ?? null;
  const price = describePriceAction(digest?.priceAction);
  const volume = describeVolume(digest?.volume);
  const factors = describeFactors(digest?.factors);
  const trendVerdict = trace.trendVerdict ?? null;
  const multiTimeframe = trace.multiTimeframeContext ?? null;
  const tactical = trace.tacticalContext ?? null;
  const wakeReason = describeWakeReason(trace.wake);
  const backlog = trace.wake?.backlog ?? null;
  const flip = trace.wake?.flip ?? null;
  const board = trace.signalBoard ?? [];
  // 早退门控**必须**含 tactical / wake / backlog / flip：漏了它们，只有战术数据、只有唤醒断面、
  // 只有补跑信息或只有翻转明细的轮次会让整段「输入信号板」消失（跑不出错、永远空白）——
  // 新增边缘态字段必须验证渲染门控没把该态排除掉。
  if (
    !board.length &&
    !regime &&
    !consensus &&
    !price &&
    !volume &&
    !factors &&
    !multiTimeframe &&
    !trendVerdict &&
    !tactical &&
    !wakeReason &&
    !backlog &&
    !flip
  ) {
    return null;
  }
  return (
    <details className="text-[11px] text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--text)]">
        输入信号板{board.length ? `（${board.length}）` : ""}
      </summary>
      <div className="mt-1 flex flex-col gap-1">
        {/* 唤醒原因的判据明细：阈值是多少、实际走了多少，一律如实展示。轮头 chip 只给标签，
            具体数值放在展开区——静默的门禁在本仓库有前科，用户必须能回答「为什么叫 / 为什么没叫」。 */}
        {wakeReason ? <div data-testid="wake-reason">{wakeReason}</div> : null}
        {backlog ? <WakeBacklogEvidence backlog={backlog} /> : null}
        {flip ? <WakeFlipEvidence flip={flip} /> : null}
        {trendVerdict ? <TrendVerdictEvidence verdict={trendVerdict} /> : null}
        {multiTimeframe ? <MultiTimeframeEvidence context={multiTimeframe} /> : null}
        {tactical ? <TacticalEvidence context={tactical} /> : null}
        {(regime || consensus) && (
          <div className="flex flex-wrap gap-x-2 gap-y-0.5">
            {regime && <span>{regime}</span>}
            {consensus && <span>{consensus}</span>}
          </div>
        )}
        {(price || volume || factors) && (
          <div className="flex flex-col gap-0.5">
            {price && <span>{price}</span>}
            {volume && <span>{volume}</span>}
            {factors && <span>{factors}</span>}
          </div>
        )}
        {board.map((s) => (
          <div key={s.strategyId} className="flex items-center justify-between gap-2">
            <span className="truncate text-[var(--text)]">{s.name}</span>
            <span className="shrink-0">
              立场 {describeStance(s.stance)}
              {s.latestSignal !== "none" && (
                <span className="text-[var(--accent-ink)]"> · {describeAiAction(s.latestSignal)}</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}

/**
 * 本轮实际喂入的交易经验（experience-context-graph §7.3）。
 *
 * **加法键，不新增 trace phase**——新增相位的代价是前端多处镜像点，`trace.py` 已有成文决策。
 * 未装配 / 零条目 ⇒ 整枚徽标不渲染。
 *
 * 展开后**逐条显示本轮真的喂进去的文本**：用户必须能看到 Agent 到底被喂了什么，
 * 否则「经验影响了这次决策」就是一句无法核对的话。
 */
function ExperienceBadge({ trace }: { trace: AiTradeRoundTrace }) {
  const wire = trace.experience ?? null;
  const items = [...(wire?.l0 ?? []), ...(wire?.l1 ?? [])];
  if (!wire || items.length === 0) return null;
  const tierText = wire.tier === "repeat"
    ? "语境未变，本轮压缩为一行"
    : wire.tier === "brief" ? "仅铁律" : "完整（语境已变化）";
  return (
    <details className="text-[11px] text-[var(--muted)]" data-testid="ai-trace-experience">
      <summary className="cursor-pointer select-none text-[var(--text)]">
        经验 ×{items.length}
        <span className="ml-1 text-[var(--muted)]">（{tierText}）</span>
      </summary>
      <div className="mt-1 flex flex-col gap-1">
        {items.map((item) => (
          <div key={item.key} className="leading-relaxed">
            <span className="text-[var(--text)]">{item.text}</span>
            <span className="ml-1 tabular text-[10px]">
              （样本 {item.support}
              {typeof item.tStat === "number" ? ` · t=${item.tStat.toFixed(1)}` : ""}
              {item.pinned ? " · 已置顶" : ""}）
            </span>
          </div>
        ))}
        {wire.disputed.length > 0 && (
          <p className="text-[10px] text-[var(--warn)]">
            另有 {wire.disputed.length} 组互相矛盾的结论被挡在纲要之外（只会进入下单前的反思环节）。
          </p>
        )}
        <p className="text-[10px]">
          这些是统计结论而非指令；与当前证据冲突时以当前证据为准。经验只进 prompt，
          不参与任何风控、仓位或下单门禁。
        </p>
      </div>
    </details>
  );
}

/** 上下文刷新节点（G2）：醒目分隔 + 可展开摘要全文，直观体现「上下文纯净」机制。 */
function SummaryNode({ summary }: { summary: string }) {
  return (
    <details className="rounded-[8px] border border-[var(--accent-soft)] bg-[var(--accent-soft)] px-2.5 py-1.5 text-[11px]">
      <summary className="cursor-pointer select-none font-medium text-[var(--accent-ink)]">
        🔄 上下文已刷新
      </summary>
      <p className="mt-1 whitespace-pre-wrap leading-relaxed text-[var(--text)]">{summary}</p>
    </details>
  );
}

/**
 * 单轮决策认知卡（ai-auto-trading-agent-trace §3.7）：轮头 → 思考/分析 → 工具调用 → 执行结果
 * → 信号板 →（到期）摘要，自上而下讲一个完整决策故事。
 */
export default function AiTradeTraceRound({ trace }: { trace: AiTradeRoundTrace }) {
  return (
    <div className="flex flex-col gap-2 rounded-[12px] border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5">
      <RoundHeader trace={trace} />
      <RetryBreadcrumb trace={trace} />
      <AiTradeReasoningBlock trace={trace} />
      <DecisionToolCard trace={trace} />
      <BaseRuleBlock trace={trace} />
      <HorizonBlock trace={trace} />
      <EdgeGateBlock trace={trace} />
      <ProfitGuardBlock trace={trace} />
      <SizingBlock trace={trace} />
      <ReflectionBlock trace={trace} />
      <ResultRow trace={trace} />
      <SignalContext trace={trace} />
      <ExperienceBadge trace={trace} />
      {trace.summary && <SummaryNode summary={trace.summary} />}
    </div>
  );
}
