"use client";

import { useMemo, useState } from "react";
import AnnualizedProjectionCard from "./AnnualizedProjectionCard";
import AutoTradePnlSummary from "@/components/Chart/AutoTradePnlSummary";
import AutoTradeRoundTripRow from "@/components/Chart/AutoTradeRoundTripRow";
import {
  type AutoTradeRange,
  type PnlSummary,
  type RoundTrip,
  buildLedger,
  filterRoundTrips,
  resolveRangeBounds,
  summarizeRoundTrips,
} from "@/lib/autoTradePnl";
import {
  formatProfitCapture,
  PROFIT_CAPTURE_HINT,
  summarizeProfitCaptureAcross,
} from "@/lib/aiTradeCapture";
import { computeReflectionCounts, latestHoldStreak } from "@/lib/aiTradeObservability";
import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";
import { colorForReturn, fmtTime, signed } from "@/lib/format";
import { useAppStore } from "@/lib/store";

// AI 自动交易统计「当前」视图（ai-trade-stats-dashboard §3.10.1）：读瞬态 store.aiTradeGroup，复用
// autoTradePnl 纯函数把各成员成交配对为完整交易往返，按时间维度汇总总收益 / 收益率 / 胜率 + 当前持仓浮盈
// （诚实降级）+ 倒序往返明细。运行期态；停止后转入「历史」持久保存。
export default function AiTradeStatsLive() {
  const group = useAppStore((s) => s.aiTradeGroup);
  const [range, setRange] = useState<AutoTradeRange>("all");
  const [custom, setCustom] = useState<{ startTs?: number; endTs?: number }>({});
  const [openRowKey, setOpenRowKey] = useState<string | null>(null);
  // 短生命周期视图：挂载期取一次 nowSec（时间窗口为日粒度，无需定时 tick；对齐 AutoTradePnlPanel）。
  const [nowSec] = useState(() => Math.floor(Date.now() / 1000));

  const capitalBaseUsd = group?.totalBudgetUsd ?? 0;

  // P1-4 当前持仓浮盈的诚实降级：position 类型为 unknown | null，须窄化守卫读取真实 unrealizedPnl 数值；
  // 仅当每个持仓成员都能取到数值时才渲染（任一不可信 → 整条略去，不显示占位 0、不猜测）。与窗口解耦。
  const unrealizedUsd = useMemo<number | null>(() => {
    if (!group) return null;
    const values = Object.values(group.members).map((m) => readUnrealizedPnl(m.position));
    return values.every((v) => v !== null)
      ? values.reduce((sum, v) => sum + (v as number), 0)
      : null;
  }, [group]);

  const { visible, summary, bounds, fullSummary } = useMemo<{
    visible: RoundTrip[];
    summary: PnlSummary;
    bounds: { startTs: number; endTs: number };
    fullSummary: PnlSummary;
  }>(() => {
    if (!group) {
      const empty = summarizeRoundTrips([], 0);
      return { visible: [], summary: empty, bounds: { startTs: 0, endTs: nowSec }, fullSummary: empty };
    }
    const members = Object.values(group.members).map((m) => ({
      instId: m.instId,
      trades: m.trades,
      leverage: group.leverage,
    }));
    const ledger = buildLedger(members);
    const b = resolveRangeBounds(range, nowSec, custom);
    const vis = filterRoundTrips(ledger, b).sort((a, c) => c.closedTs - a.closedTs);
    // P2-5：年化口径与时间筛选解耦——用**未筛选全会话**汇总（returnPct/tradeCount），避免切窗口令年化跳变。
    return {
      visible: vis,
      summary: summarizeRoundTrips(vis, capitalBaseUsd),
      bounds: b,
      fullSummary: summarizeRoundTrips(ledger, capitalBaseUsd),
    };
  }, [group, range, custom, nowSec, capitalBaseUsd]);

  // F4 利润捕获率：本轮迭代循环的验收指标——**峰值浮盈里最终落袋了几成**。与已实现收益的时间窗口
  // 解耦（配对口径与后端 _summarize_pairs 逐字对应），故取全会话样本。
  const capture = useMemo(
    () =>
      summarizeProfitCaptureAcross(
        group ? Object.values(group.members).map((m) => ({ trades: m.trades })) : [],
      ),
    [group],
  );

  if (group == null) {
    return (
      <EmptyHint text="尚未启动 AI 自动交易；启动后这里按时间维度汇总每次完整交易（开→平）的收益与总收益率，停止后可在『历史』标签回溯。" />
    );
  }

  // §3.2 双栏骨架：左栏承载统计数据（起始提示 / 运行观测 / KPI 概览 / 预计年化 / 持仓浮盈），
  // 右栏承载交易记录（倒序完整往返，可展开行内 K 线）。窄屏（<md）上下堆叠。左右各自 `overflow-y-auto`。
  return (
    <div className="flex min-h-0 flex-1 gap-4 max-md:flex-col">
      <aside className="flex w-[40%] shrink-0 flex-col gap-3 overflow-y-auto pr-1 max-md:w-full max-md:overflow-visible">
        <p className="rounded-md border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5 text-[10px] leading-relaxed text-[var(--muted)]">
          数据自 <span className="tabular text-[var(--text)]">{fmtTime(group.startedTs)}</span> 起记录
          （运行期态；整组停止后转入「历史」标签持久保存）。
        </p>
        <div className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
          <p className="text-[10px] text-[var(--muted)]">
            运行观测 · 反思计数为近 50 轮样本口径；唤醒抑制 / 连续观望为会话级实时值
          </p>
          {Object.values(group.members).map((m) => (
            <MemberObservabilityRow key={m.instId} member={m} />
          ))}
        </div>
        <AutoTradePnlSummary
          range={range}
          onRangeChange={setRange}
          custom={custom}
          onCustomChange={setCustom}
          summary={summary}
          kpiCols={2}
        />
        <AnnualizedProjectionCard
          returnPct={fullSummary.totalReturnPct}
          elapsedSec={nowSec - group.startedTs}
          tradeCount={fullSummary.count}
        />
        <ProfitCaptureCard capture={capture} />
        {unrealizedUsd !== null && (
          <div className="flex items-center justify-between rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs">
            <span className="text-[var(--muted)]">
              当前持仓浮盈（未实现，全局）
              <span className="ml-1 text-[10px]">· 与时间窗口无关，不计入上方已实现总收益</span>
            </span>
            <span className="tabular font-semibold" style={{ color: colorForReturn(unrealizedUsd) }}>
              {signed(unrealizedUsd)} USDT
            </span>
          </div>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col gap-2 overflow-y-auto max-md:overflow-visible">
        <h3 className="text-[10px] font-medium uppercase tracking-wide text-[var(--muted)]">交易记录</h3>
        {visible.length === 0 ? (
          <WindowEmpty earlierThanStart={bounds.endTs < group.startedTs} startedTs={group.startedTs} />
        ) : (
          <div className="overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface)]">
            {visible.map((rt) => {
              const key = rowKey(rt);
              return (
                <AutoTradeRoundTripRow
                  key={key}
                  rt={rt}
                  open={openRowKey === key}
                  onToggle={() => setOpenRowKey((prev) => (prev === key ? null : key))}
                  bar={group.bar}
                />
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

// O5（hold-inertia-observability）：逐成员运行观测行（纯呈现，无状态）。反思计数从 decision 环形
// （≤50 条，与后端同窗）按后端镜像口径派生；唤醒抑制 / 连续观望缺失呈现「—」（老后端 / WS 未连上 /
// REST 首刷 info() 不带两键的短暂窗口，R2-05 有意降级，走同一呈现路径无需特判）。
function MemberObservabilityRow({ member }: { member: AiTradeGroupMemberState }) {
  const counts = computeReflectionCounts(member.decisions);
  const wakeSkips = member.session?.wakeSkips;
  const holdStreak = latestHoldStreak(member.decisions, member.session);
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-[11px]">
      <span className="font-medium text-[var(--text)]">{member.instId}</span>
      <span className="tabular text-[var(--muted)]">
        反思 {counts.runs}（放行 {counts.confirms} · 否决 {counts.aborts} · 故障 {counts.failClosed}）
        · 唤醒抑制 {wakeSkips ?? "—"} · 连续观望 {holdStreak === null ? "—" : `${holdStreak} 轮`}
      </span>
    </div>
  );
}

// 窄化守卫：position 为 unknown | null，仅取真实 number 型 unrealizedPnl（WS position 载荷承载浮盈的字段，
// 已核实 executor.position_payload 出该键）；flat（null）→ 0；有持仓但读不到数值 → null（不可信、触发整条略去）。
function readUnrealizedPnl(position: unknown): number | null {
  if (position == null) return 0;
  if (
    typeof position === "object" &&
    typeof (position as { unrealizedPnl?: unknown }).unrealizedPnl === "number"
  ) {
    return (position as { unrealizedPnl: number }).unrealizedPnl;
  }
  return null;
}

function WindowEmpty({ earlierThanStart, startedTs }: { earlierThanStart: boolean; startedTs: number }) {
  if (earlierThanStart) {
    return (
      <EmptyHint
        text={`所选区间早于本组启动时间（${fmtTime(startedTs)}），暂无记录；更早的会话请见「历史」标签。`}
      />
    );
  }
  return (
    <EmptyHint text="当前时间窗口内暂无完整交易（一次完整交易 = 一对开仓→平仓）；持仓平仓后将在此按收益率汇总。" />
  );
}

function EmptyHint({ text }: { text: string }) {
  return <p className="px-3 py-8 text-center text-xs leading-relaxed text-[var(--muted)]">{text}</p>;
}

// 稳定行 key：优先成交 id，回退开 / 平时刻，永不跨标的混淆。
function rowKey(rt: RoundTrip): string {
  return `${rt.instId}-${rt.entry.id ?? rt.openedTs}-${rt.exit.id ?? rt.closedTs}`;
}

/**
 * 利润捕获率卡片（F4）：已实现盈亏 ÷ 峰值浮盈。
 *
 * 口径提示是**必需**的而非装饰：分母不含手续费、分子含，小额往返会系统性偏低；持仓期间加过仓的
 * 往返，峰值是按最终均价重估值。不写出来就是误导（R-8）。样本 < 3 显示「样本不足」，绝不用 1~2 笔
 * 冒充统计结论（沿用统计看板既有的最小样本门禁范式）。
 */
function ProfitCaptureCard({
  capture,
}: {
  capture: ReturnType<typeof summarizeProfitCaptureAcross>;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-[var(--muted)]">利润捕获率</span>
        <span className="tabular font-semibold text-[var(--text)]" data-testid="ai-profit-capture">
          {formatProfitCapture(capture)}
        </span>
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">{PROFIT_CAPTURE_HINT}</p>
    </div>
  );
}
