"use client";

import { useState } from "react";
import {
  type AiTraceTone,
  describeAiAction,
  describeBaseRule,
  describeConsensus,
  describeDegradeReason,
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
  describeStance,
  isEffectiveExitCommitted,
  describeTraceState,
  describeTrigger,
  describeVolume,
} from "@/lib/aiTrade";
import type { AiTradeRoundTrace, MultiTimeframeContext } from "@/lib/aiTradeTypes";
import { fmtNum, fmtPrice, fmtTime } from "@/lib/format";
import { renderMarkdown } from "@/lib/markdown";

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
      style={{ color: active ? "var(--accent)" : "var(--muted)" }}
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

/** 思考/分析：深度思考折叠置于其上；分析流式走 pre-wrap + 光标，轮结束走安全 markdown。 */
function AnalysisBlock({ trace }: { trace: AiTradeRoundTrace }) {
  const streaming = trace.state === "deciding";
  const hasAnalysis = trace.analysis.length > 0;
  if (!hasAnalysis && !streaming && !trace.thinking) return null;
  return (
    <div className="flex flex-col gap-1.5">
      {trace.thinking && (
        <details className="text-[11px] text-[var(--muted)]">
          <summary className="cursor-pointer select-none text-[var(--text)]">深度思考</summary>
          <p className="mt-1 whitespace-pre-wrap leading-relaxed">{trace.thinking}</p>
        </details>
      )}
      {(hasAnalysis || streaming) && (
        <div className="rounded-[10px] bg-[var(--surface-2)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--text)]">
          {!hasAnalysis ? (
            <span className="text-[var(--muted)]">正在研判…</span>
          ) : streaming ? (
            <span className="whitespace-pre-wrap">
              {trace.analysis}
              <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-current align-middle" />
            </span>
          ) : (
            <div className="flex flex-col">{renderMarkdown(trace.analysis)}</div>
          )}
        </div>
      )}
    </div>
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
        <p className="text-[10px] leading-relaxed text-[var(--accent)]" data-testid="trace-profit-cooldown">
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
          ) : streaming ? (
            <span className="whitespace-pre-wrap">
              {text}
              <span className="ml-0.5 inline-block h-3 w-[2px] translate-y-0.5 animate-pulse bg-current align-middle" />
            </span>
          ) : (
            <div className="flex flex-col">{renderMarkdown(text)}</div>
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
      <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[11px] text-[var(--short)]">
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

/** 输入信号板 / 中线背景 / 体制 / 共识 / 市场证据摘要。 */
function SignalContext({ trace }: { trace: AiTradeRoundTrace }) {
  const regime = describeRegime(trace.marketContext);
  const consensus = describeConsensus(trace.consensus ?? null);
  const digest = trace.marketDigest ?? null;
  const price = describePriceAction(digest?.priceAction);
  const volume = describeVolume(digest?.volume);
  const factors = describeFactors(digest?.factors);
  const multiTimeframe = trace.multiTimeframeContext ?? null;
  const board = trace.signalBoard ?? [];
  if (!board.length && !regime && !consensus && !price && !volume && !factors && !multiTimeframe) {
    return null;
  }
  return (
    <details className="text-[11px] text-[var(--muted)]">
      <summary className="cursor-pointer select-none text-[var(--text)]">
        输入信号板{board.length ? `（${board.length}）` : ""}
      </summary>
      <div className="mt-1 flex flex-col gap-1">
        {multiTimeframe ? <MultiTimeframeEvidence context={multiTimeframe} /> : null}
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
                <span className="text-[var(--accent)]"> · {describeAiAction(s.latestSignal)}</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}

/** 上下文刷新节点（G2）：醒目分隔 + 可展开摘要全文，直观体现「上下文纯净」机制。 */
function SummaryNode({ summary }: { summary: string }) {
  return (
    <details className="rounded-[8px] border border-[var(--accent-soft)] bg-[var(--accent-soft)] px-2.5 py-1.5 text-[11px]">
      <summary className="cursor-pointer select-none font-medium text-[var(--accent)]">
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
      <AnalysisBlock trace={trace} />
      <DecisionToolCard trace={trace} />
      <BaseRuleBlock trace={trace} />
      <ProfitGuardBlock trace={trace} />
      <ReflectionBlock trace={trace} />
      <ResultRow trace={trace} />
      <SignalContext trace={trace} />
      {trace.summary && <SummaryNode summary={trace.summary} />}
    </div>
  );
}
