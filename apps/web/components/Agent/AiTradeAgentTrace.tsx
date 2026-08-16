"use client";

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import { describeConsensus, describeRegime, describeStance } from "@/lib/aiTrade";
import type { AiTradeGroupState, AiTradeRoundTrace, AiTradeSessionRow } from "@/lib/aiTradeTypes";
import { fmtMoney, fmtNum } from "@/lib/format";
import AiTradeTraceRound from "./AiTradeTraceRound";

// 距底阈值（px）：镜像 AgentMessageList 的贴底跟随 pin 逻辑。
const PIN_THRESHOLD = 48;
// 用户离底后静置达此时长即自动恢复贴底跟随（第 3 轮 R2：2 分钟）。
const AUTO_FOLLOW_RESUME_MS = 120_000;

/** 会话概览条：标的·周期、状态、体制、累计盈亏、持仓/预算/距上下文刷新/共识、降级提示。 */
function TraceOverview({ session }: { session: AiTradeSessionRow }) {
  const pnl = session.sessionPnlUsd ?? 0;
  const stopped = session.state === "stopped";
  const regime = describeRegime(session.lastDecision?.marketContext);
  const consensus = describeConsensus(session.lastDecision?.consensus ?? null);
  const roundsToSummary = Math.max(
    0,
    (session.contextRefreshRounds ?? 0) - (session.roundsSinceSummary ?? 0),
  );
  return (
    <div className="flex flex-col gap-1.5 border-b border-[var(--border)] px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-[var(--text)]">
            {session.instId} · {session.bar}
          </span>
          <Badge tone={stopped ? "neutral" : "long"}>{stopped ? "已停止" : "运行中"}</Badge>
          {regime && <Badge tone="neutral">{regime}</Badge>}
        </div>
        <span
          className={
            pnl >= 0
              ? "tabular text-xs font-medium text-[var(--long-ink)]"
              : "tabular text-xs font-medium text-[var(--short-ink)]"
          }
        >
          {pnl >= 0 ? "+" : ""}
          {fmtMoney(pnl)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-[var(--muted)]">
        <span>持仓 {describeStance(session.positionSide)}</span>
        <span>
          预算 {fmtNum(session.budgetUsd)} · {session.leverage}×
        </span>
        {roundsToSummary > 0 ? <span>距上下文刷新 {roundsToSummary} 根K线</span> : null}
        {consensus && <span>{consensus}</span>}
      </div>
      {session.degraded && (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1 text-[10px] text-[var(--short-ink)]">
          决策降级观望（多次重试仍失败 / 置信度不足 / 未产出有效决策 → 本轮回落观望，资金安全优先）
        </p>
      )}
    </div>
  );
}

/** 空态：会话运行中但尚无轨迹（等待首个确认 K 线）/ 已停止无轨迹。 */
function TraceEmpty({ stopped }: { stopped: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1.5 px-4 py-10 text-center text-[11px] text-[var(--muted)]">
      <span className="text-lg">🤖</span>
      {stopped ? (
        <span>会话已停止，本次运行没有产生决策轨迹。</span>
      ) : (
        <span>AI 正在等待首个确认 K 线，随后每根 K 线一次决策，轨迹将实时呈现在此。</span>
      )}
    </div>
  );
}

/** 分组成员段控：多标的时顶部切换要查看轨迹的标的（各成员轨迹独立留存，切换不丢历史）。 */
function SymbolTabs({
  instIds,
  focused,
  onSelect,
}: {
  instIds: string[];
  focused: string;
  onSelect: (instId: string) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="自动交易标的"
      className="flex flex-wrap items-center gap-1 border-b border-[var(--border)] px-3 py-1.5"
    >
      {instIds.map((instId) => (
        <button
          key={instId}
          type="button"
          role="tab"
          aria-selected={instId === focused}
          onClick={() => onSelect(instId)}
          className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${
            instId === focused
              ? "bg-[var(--surface)] text-[var(--text)] shadow-sm"
              : "text-[var(--muted)] hover:text-[var(--text)]"
          }`}
        >
          {instId}
        </button>
      ))}
    </div>
  );
}

/**
 * AI 自动交易「决策执行轨迹」视图（ai-trade-multi-symbol-and-timeframe §4.2）：右侧 AI 助手面板分段视图之一。
 * 多标的分组：顶部标的段控（默认聚焦当前图表标的·若在组内否则首成员）；展示聚焦成员的逐轮轨迹。
 * 概览条 + 轮次时间线（贴底跟随 + aria-live 播报流式增量 + 「↓ 回到最新」，尊重用户上滚）。单成员时不显段控。
 */
export default function AiTradeAgentTrace({
  group,
  activeInstId,
}: {
  group: AiTradeGroupState | null;
  activeInstId: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pinned, setPinned] = useState(true);
  const [focused, setFocused] = useState<string | null>(null);

  // 空闲恢复计时器：用户离底浏览历史后启动，静置满 AUTO_FOLLOW_RESUME_MS 自动交还「跟随最新」（R2）。
  const clearIdleTimer = () => {
    if (idleTimerRef.current != null) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
  };

  const armIdleTimer = () => {
    clearIdleTimer();
    idleTimerRef.current = setTimeout(() => {
      idleTimerRef.current = null;
      setPinned(true); // 恢复跟随；下方 [signature, pinned] effect 会滚到最新
    }, AUTO_FOLLOW_RESUME_MS);
  };

  const instIds = group ? Object.keys(group.members) : [];
  const instKey = instIds.join(",");

  // 默认聚焦当前图表标的（若在组内）否则首成员；成员集变化后若聚焦项消失则复位（切换不丢历史）。
  useEffect(() => {
    if (!group) {
      setFocused(null);
      return;
    }
    setFocused((prev) =>
      prev && instIds.includes(prev)
        ? prev
        : instIds.includes(activeInstId)
          ? activeInstId
          : (instIds[0] ?? null),
    );
  }, [group?.groupId, instKey, activeInstId]); // eslint-disable-line react-hooks/exhaustive-deps

  const focusedMember = group && focused ? group.members[focused] : null;
  const session = focusedMember?.session ?? null;
  const traces: AiTradeRoundTrace[] = focusedMember?.traces ?? [];

  // 流式增量到达即变化（累积文本长度 / 状态 / 工具卡到位）；含聚焦标的，切换即重新贴底。
  const signature =
    focused +
    "|" +
    traces
      .map((t) => `${t.round}:${t.thinking.length}:${t.analysis.length}:${t.state}:${t.toolCall ? 1 : 0}`)
      .join("|");

  useEffect(() => {
    if (pinned) endRef.current?.scrollIntoView({ block: "end" });
  }, [signature, pinned]);

  // 卸载清理，避免计时器泄漏。
  useEffect(() => () => clearIdleTimer(), []);
  // 切换聚焦标的：复位为跟随并清除上一标的的倒计时（切换即看新标的最新记录）。
  useEffect(() => {
    clearIdleTimer();
    setPinned(true);
  }, [focused]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!group || !session) return null;
  const stopped = session.state === "stopped";

  const onScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < PIN_THRESHOLD;
    setPinned(nearBottom);
    if (nearBottom) clearIdleTimer(); // 回到底部 → 跟随，取消恢复倒计时
    else armIdleTimer(); // 离底浏览历史 → (重)启恢复倒计时
  };

  const jumpToLatest = () => {
    clearIdleTimer();
    setPinned(true);
    endRef.current?.scrollIntoView({ block: "end" });
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {instIds.length > 1 && focused && (
        <SymbolTabs instIds={instIds} focused={focused} onSelect={setFocused} />
      )}
      <TraceOverview session={session} />
      <div
        ref={containerRef}
        onScroll={onScroll}
        data-testid="ai-trade-trace-scroll"
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          className="flex flex-col gap-2.5 px-3 py-3"
        >
          {traces.length === 0 ? (
            <TraceEmpty stopped={stopped} />
          ) : (
            traces.map((t) => <AiTradeTraceRound key={`${t.round}-${t.ts}`} trace={t} />)
          )}
          <div ref={endRef} />
        </div>
      </div>
      {!pinned && (
        <button
          type="button"
          onClick={jumpToLatest}
          aria-label="回到最新"
          className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-1 text-[11px] text-[var(--text)] shadow-soft transition-colors duration-150 ease-smooth hover:bg-[var(--surface-2)]"
        >
          ↓ 回到最新
        </button>
      )}
    </div>
  );
}
