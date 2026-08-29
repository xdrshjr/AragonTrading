"use client";

import { useCallback, useMemo, useState } from "react";
import AnnualizedProjectionCard from "./AnnualizedProjectionCard";
import DisciplineCard from "./DisciplineCard";
import ExitSourceCard from "./ExitSourceCard";
import ExperienceCard from "./ExperienceCard";
import HorizonAcceptanceCard from "./HorizonAcceptanceCard";
import DirectionBalanceCard from "./DirectionBalanceCard";
import OpenFunnelCard from "./OpenFunnelCard";
import TradabilityCard from "./TradabilityCard";
import TurnoverBackstopCard, { type RepairPendingState } from "./TurnoverBackstopCard";
import WakeAttributionCard from "./WakeAttributionCard";
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
import { memberDisciplines } from "@/lib/aiTradeDiscipline";
import { formatHoldDuration, horizonLiveKpis } from "@/lib/aiTradeHorizon";
import { computeReflectionCounts, latestHoldStreak } from "@/lib/aiTradeObservability";
import type {
  AiTradeGroupMemberState,
  AiTradeGroupState,
  AiTradeSessionRow,
} from "@/lib/aiTradeTypes";
import {
  type AiTradeRealizedStats,
  realizedCoverageText,
  realizedRows,
} from "@/lib/aiTradeProfitability";
import { aiTradeApi } from "@/lib/aiTradeApi";
import { ApiRequestError } from "@/lib/api";
import { colorForReturn, fmtTime, signed } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import { TRADES_MAX } from "@/lib/wireCaps";

// AI 自动交易统计「当前」视图（ai-trade-stats-dashboard §3.10.1）：读瞬态 store.aiTradeGroup，复用
// autoTradePnl 纯函数把各成员成交配对为完整交易往返，按时间维度汇总总收益 / 收益率 / 胜率 + 当前持仓浮盈
// （诚实降级）+ 倒序往返明细。运行期态；停止后转入「历史」持久保存。
export default function AiTradeStatsLive() {
  const group = useAppStore((s) => s.aiTradeGroup);
  const [range, setRange] = useState<AutoTradeRange>("all");
  // 「立即补挂」的请求态按 `instId` 键（[P2-5]：卡片保持无状态）。状态流转本身由后端在
  // `state`/`blockedReason` 变化时补推 status 带上来（[P1-3]），此处只负责按钮的三态。
  const [repairState, setRepairState] = useState<Record<string, RepairPendingState>>({});
  const groupId = group?.groupId ?? null;
  const repair = useCallback(
    (instId: string) => {
      if (!groupId) return;
      const settle = (result: { ok: boolean; message: string }) => {
        setRepairState((prev) => ({ ...prev, [instId]: { result } }));
        // 结果只停留 2.5 秒：状态流转本身由后端补推的 status 带上来，按钮不该永久停在「已提交」。
        setTimeout(() => setRepairState(({ [instId]: _done, ...rest }) => rest), 2500);
      };
      setRepairState((prev) => ({ ...prev, [instId]: { pending: true } }));
      aiTradeApi
        .repairBackstop(groupId, instId)
        .then(() => settle({ ok: true, message: "补挂请求已发出，正在向交易所核实" }))
        .catch((err: unknown) => settle({ ok: false, message: repairErrorText(err) }));
    },
    [groupId],
  );
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

  // 轴 D 取**未筛选**的 ledger 是刻意的——与 fullSummary / AnnualizedProjectionCard 同源：
  // 胜率归因的样本量本来就少（MIN_SAMPLES=5），再叠一层时间筛选会让这张表在绝大多数窗口下整卡消失。
  const { visible, summary, bounds, fullSummary, ledger } = useMemo<{
    visible: RoundTrip[];
    summary: PnlSummary;
    bounds: { startTs: number; endTs: number };
    fullSummary: PnlSummary;
    ledger: RoundTrip[];
  }>(() => {
    if (!group) {
      const empty = summarizeRoundTrips([], 0);
      return {
        visible: [],
        summary: empty,
        bounds: { startTs: 0, endTs: nowSec },
        fullSummary: empty,
        ledger: [],
      };
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
      ledger,
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

  // D4：`fullSummary` 与 `capture` 都由 `member.trades` 直接求和，而那份缓冲有 wire 封顶。
  // **统计的分母变了就必须说**——否则一个交易产品会在报一个它自己都不相信的收益率。
  const tradesCapped = useMemo(
    () => Object.values(group?.members ?? {}).some((m) => m.trades.length >= TRADES_MAX),
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
        {tradesCapped && (
          <p
            data-testid="ai-trade-stats-cap-notice"
            className="rounded-md border border-[var(--warn)] bg-[var(--warn-soft)] px-2.5 py-1.5 text-[10px] leading-relaxed text-[var(--warn)]"
          >
            口径：最近 {TRADES_MAX} 笔成交。实时缓冲已达上限，下方「全会话」总收益、成交笔数、
            预计年化与利润捕获率均按此口径计算；权威全量口径见「历史」标签。
          </p>
        )}
        {/* 暂停期口径提示（ai-trade-pause-resume §3.9.4）：不加这一句，一张「决策轮数不再增长」
            的看板会被读成「AI 停摆 / 坏了」——而暂停期不起决策轮是**预期行为**，仍在跑的是盯市
            与保护层。这条提示只解释口径，不复述任何判定性结论。 */}
        {group.state === "paused" && (
          <p
            data-testid="ai-trade-stats-paused-hint"
            className="rounded-md border border-[var(--accent)] bg-[var(--accent-soft)] px-2.5 py-1.5 text-[10px] leading-relaxed text-[var(--accent-ink)]"
          >
            已暂停：决策轮与开仓已停止，故下面的轮数 / 漏斗 / 反思计数不再增长；
            持仓浮盈、保护性平仓与交易所侧兜底核实仍在运行，往返明细也会照常追加。
          </p>
        )}
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
        {/* 生效纪律自检（discipline-preset 轴 B）：紧跟在 KPI 之后——「AI 为什么开单变少」是用户
            看完收益数字后的第一个问题，答案必须就在下一屏，而不是藏在更下面。数据源优先取**成员级**
            status（`exchangeBackstop` 是唯一逐成员分叉的一行），分组回执只做兜底。 */}
        <DisciplineCard
          wire={
            group.discipline
              ? {
                  ...group.discipline,
                  ...(memberDisciplines(
                    Object.values(group.members)[0]?.session?.disciplines,
                    group.discipline,
                  ) ?? { rows: [], hint: null }),
                }
              : null
          }
          memberCount={Object.keys(group.members).length}
          summaryHealth={Object.values(group.members)[0]?.session?.summaryHealth}
          direction={group.direction}
        />
        {/* 可交易性预检 + 开仓漏斗（tradability-and-open-funnel 轴 A / 轴 C）：紧跟纪律卡——
            「AI 为什么不开单」的完整答案是这三张卡连起来读的（哪些闸开着 → 这个周期上它们会拦掉
            多少 → 模型真的提出过几次开仓、被谁拦下）。数据源是**成员级实时快照**，逐成员一份。 */}
        {Object.values(group.members).map((m) => (
          <TradabilityCard key={`trad-${m.instId}`} tradability={m.session?.tradability} />
        ))}
        {Object.values(group.members).map((m) => (
          <OpenFunnelCard key={`funnel-${m.instId}`} funnel={m.session?.openFunnel} />
        ))}
        {/* 方向分布（directional-symmetry 轴 D）：紧贴漏斗卡下方——两者是同一个问题的两半，
            漏斗说「被哪一层拦下」，本卡说「拦掉的是哪个方向」。老会话 / 总闸关 ⇒ 整卡不渲染。 */}
        {Object.values(group.members).map((m) => (
          <DirectionBalanceCard key={`dir-${m.instId}`} funnel={m.session?.openFunnel} />
        ))}
        {/* 经验模式（experience-context-graph §7.3）：数据源同为**成员级实时快照**。
            未装配 ⇒ 整卡不渲染（`ExperienceCard` 自己门控），绝不挂一排「—」。 */}
        {Object.values(group.members).map((m) => (
          <ExperienceCard key={`exp-${m.instId}`} experience={m.session?.experience} />
        ))}
        <HorizonLiveCard group={group} ledger={ledger} />
        {/* 周期验收闭环（horizon-acceptance）：两卡是**两个独立入参**——`acceptance` 为 null
            （未装配周期模式的对照臂）时退出来源卡照常渲染（[P0-4]）。分组合并口径由
            `useAiTradeGroupStream` 的 15s 轮询经窄 action 补丁进来，故它会随会话推进而更新。 */}
        <HorizonAcceptanceCard acceptance={group.acceptance} />
        {/* 换手纪律与兜底（turnover-budget §2.4.2）：数据源是**成员级实时快照**
            `member.session.horizon`——`group.tradingHorizon` 是分组共享的**契约**、不是快照
            （[P1-2]）。每成员一份令牌桶与一份覆盖率，绝不跨成员求和。 */}
        <TurnoverBackstopCard
          members={Object.values(group.members).map((m) => ({
            symbol: m.instId,
            turnover: m.session?.horizon?.turnover,
            backstop: m.session?.horizon?.backstop,
          }))}
          onRepair={repair}
          repairPending={repairState}
        />
        <ExitSourceCard exits={group.exits} />
        <ProfitCaptureCard capture={capture} />
        <RealizedLiveCard group={group} />
        <WakeAttributionCard trips={ledger} />
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
                  showR
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
  const scheduler = describeWakeScheduler(member.session);
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-[11px]">
      <span className="font-medium text-[var(--text)]">{member.instId}</span>
      <span className="tabular text-[var(--muted)]">
        反思 {counts.runs}（放行 {counts.confirms} · 否决 {counts.aborts} · 故障 {counts.failClosed}）
        · 唤醒抑制 {wakeSkips ?? "—"} · 连续观望 {holdStreak === null ? "—" : `${holdStreak} 轮`}
        {scheduler ? (
          <span data-testid="wake-scheduler-counts" title={WAKE_SCHEDULER_HINT}>
            {" "}
            · {scheduler}
          </span>
        ) : null}
      </span>
    </div>
  );
}

// wakeDrops 的口径是「**到点了却撞上在飞轮**」，其中一部分即便不在飞也会被守卫跳过——不写清楚，用户
// 会把它读成「系统漏掉了这么多次决策」（wake-scheduler-closed-loop §4.3 点名要求的那条 tooltip）。
const WAKE_SCHEDULER_HINT =
  "补跑 A/B：B = 到点却撞上在飞轮的唤醒次数（其中一部分即便不在飞也会被守卫跳过，故它不等于" +
  "「系统漏掉了这么多次决策」），A = 其中真的补跑了一轮的次数。" +
  "持仓省 = 保护线在岗（armed）时跳过的持仓盯市轮；翻转唤 = 未收线预演出立场翻转而起的轮。";

// 唤醒调度闭环四计数（§4.3）。四个数全为 0 ⇒ 返回 null ⇒ **整段不出现**：轴未生效的会话不该看到
// 一排 0，那会被读成「统计坏了」——与本页「老记录缺键即整行隐藏」的既有纪律同款。
function describeWakeScheduler(session: AiTradeSessionRow | null | undefined): string | null {
  const drops = session?.wakeDrops ?? 0;
  const catchups = session?.wakeCatchups ?? 0;
  const positionWatchSkips = session?.positionWatchSkips ?? 0;
  const signalFlipWakes = session?.signalFlipWakes ?? 0;
  const parts: string[] = [];
  if (drops || catchups) parts.push(`补跑 ${catchups}/${drops}`);
  if (positionWatchSkips) parts.push(`持仓省 ${positionWatchSkips}`);
  if (signalFlipWakes) parts.push(`翻转唤 ${signalFlipWakes}`);
  return parts.length ? parts.join(" · ") : null;
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
 * 交易周期卡片（ai-trade-holding-horizon-modes §2.11 展示落点之三 · 运行期侧）。
 *
 * 未装配本层的会话 ⇒ `tradingHorizon` 为空 ⇒ **整卡不渲染**（绝不在一个没有周期契约的会话上挂出
 * 一排 0，那会被读成「统计坏了」——与本页既有纪律同款）。
 *
 * 口径提示是**必需**的而非装饰：这里的三个数由成交时间戳配对现算，与落库口径（`note_close` 墙钟
 * 差）量级一致但不保证逐笔相等；且未平仓的持仓不计入。不写出来就是误导。
 */
function HorizonLiveCard({ group, ledger }: { group: AiTradeGroupState; ledger: RoundTrip[] }) {
  const rule = group.tradingHorizon ?? null;
  if (!rule || !rule.enabled) return null;
  const holds = ledger.map((rt) => rt.closedTs - rt.openedTs);
  const target =
    rule.targetHoldMaxSeconds && rule.targetHoldMaxSeconds > 0
      ? `${formatHoldDuration(rule.targetHoldMinSeconds)} – ${formatHoldDuration(rule.targetHoldMaxSeconds)}`
      : "未设定";
  return (
    <div
      className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-horizon-live"
    >
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-[var(--muted)]">交易周期 · {rule.title ?? rule.profile}</span>
        <span className="tabular text-[10px] text-[var(--muted)]">目标持仓 {target}</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {horizonLiveKpis(holds, rule).map((kpi) => (
          <div key={kpi.label} className="flex flex-col">
            <span className="text-[10px] text-[var(--muted)]">{kpi.label}</span>
            <span className="tabular text-xs font-semibold text-[var(--text)]">{kpi.value}</span>
          </div>
        ))}
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        运行期估算：按成交时间戳配对的
        <strong className="text-[var(--text)]">已完成往返</strong>
        现算，未平仓的持仓不计入；会话停止后以「历史」标签的落库值（含本层拦截计数与豁免明细）为准。
      </p>
    </div>
  );
}

/**
 * 会话级「已实现」卡（realized-edge-calibration [P1-2] 的渲染落点之二）。
 *
 * 与历史详情页**共用 `lib/aiTradeProfitability` 的同一套格式化器**——同一个读数在两处各写一份
 * 口径，正是本仓库反复出事的形状。多成员时逐成员一行（比例类读数不可跨成员平均）。
 * 样本 < 3 笔的成员整行不渲染；一个成员都渲染不出 ⇒ 整卡不出现。
 */
function RealizedLiveCard({ group }: { group: AiTradeGroupState }) {
  const rows = Object.values(group.members)
    .map((m) => ({ instId: m.instId, stats: m.session?.realized ?? null }))
    .filter((row): row is { instId: string; stats: AiTradeRealizedStats } =>
      realizedRows(row.stats) !== null,
    );
  if (rows.length === 0) return null;
  return (
    <div
      className="flex flex-col gap-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-realized-live"
    >
      {/* 标题不写「估计 ↔ 实现」：本卡渲染的三项全是**已实现**侧的读数，估计与实现的对拍在轴 C
          的校准卡（跨会话、带 coverage），一个会话的样本量撑不起那个结论。 */}
      <span className="text-xs text-[var(--muted)]">本会话已实现（逐笔往返结算）</span>
      {rows.map(({ instId, stats }) => (
        <div key={instId} className="flex flex-col gap-0.5">
          <span className="tabular text-[11px] text-[var(--text)]">
            {instId} · {(realizedRows(stats) ?? []).map((r) => `${r.label} ${r.value}`).join(" · ")}
          </span>
          <span className="tabular text-[10px] text-[var(--muted)]">{realizedCoverageText(stats)}</span>
        </div>
      ))}
      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        R 倍数 = 已实现净额 ÷ 计划风险单位（开仓价到止损价的距离）；无止损或合约乘数未知的往返不进分母。
      </p>
    </div>
  );
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

/**
 * 补挂失败的人话（**绝不把裸码或原始异常抛给用户**）。
 *
 * 被三道锁拦下时后端回 409 + `blockedReason`，那份 message 本身已是中文；交易所拒单回 502 +
 * 原始 sCode，此时把码带上——它是用户去交易所后台核对时唯一有用的线索。
 */
function repairErrorText(err: unknown): string {
  if (err instanceof ApiRequestError) {
    return err.code ? `${err.message}（${err.code}）` : err.message;
  }
  return "补挂请求失败，请稍后重试";
}
