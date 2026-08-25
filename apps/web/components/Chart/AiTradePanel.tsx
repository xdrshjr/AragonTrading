"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import {
  AI_TRADE_PAUSE_REASON_TEXT,
  describeAiTradeLifecycleBadge,
  describeCommittedExit,
  describeProfitGuardCooldown,
  describeProfitGuardFallback,
  describeProfitGuardStatus,
  describeProfitGuardTrail,
  describeStopReason,
  formatNextWake,
  formatPausedFor,
  isEffectiveExitCommitted,
  resolveAiTradeArmed,
  resolveAiTradeLifecycle,
} from "@/lib/aiTrade";
import { aiTradeApi } from "@/lib/aiTradeApi";
import {
  disciplineRow,
  memberDisciplines,
  summarizeDisciplines,
} from "@/lib/aiTradeDiscipline";
import { describeHorizonStatus } from "@/lib/aiTradeHorizon";
import { AiTradeExitCard } from "./AiTradeExitCard";
import { describeTradability } from "@/lib/aiTradeTradability";
import { describeTurnoverChip } from "@/lib/aiTradeTurnover";
import { COST_BASIS_HINT, describeCostBasis } from "@/lib/aiTradeCost";
import { colorForReturn, fmtMoney, fmtNum, fmtPct, fmtPrice } from "@/lib/format";
import { useAppStore } from "@/lib/store";
import { ApiRequestError } from "@/lib/api";
import type {
  AiTradeBackstopAtPause,
  AiTradeDisciplineRow,
  AiTradeGroupMemberState,
  AiTradeGroupState,
  AiTradeNotArmedRow,
  AiTradeResumableGroup,
  ProfitGuardSnapshot,
} from "@/lib/aiTradeTypes";
import type { Instrument } from "@/lib/types";

// AI 自动交易「标的面板」（ai-trade-multi-symbol-and-timeframe §4.2）：泛化为「多标的」分组视图。
// Agent 的思考 / 分析 / 工具调用 / 决策日志由右侧「AI 助手 → 执行轨迹」承接；左侧此处只呈现标的：
// 分组头（标签 + 运行徽标 + 分组累计盈亏＝Σ 成员 PnL）+ 逐成员「自动交易标的」行（可点击切主图 +
// 持仓徽标 + 命中自选时富化行情）。单成员时视觉与第 5 轮单会话面板等价。
interface AiTradePanelProps {
  rows: Instrument[]; // 已 resolve 的自选行（用于富化成员标的行行情）
  active: string; // 当前选中 instId（判断是否用 livePrice 覆盖快照价）
  livePrice?: number | null;
  changePct?: number | null;
  onSelect: (instId: string) => void; // 点击成员标的 → setInst 切图
}

export default function AiTradePanel({
  rows,
  active,
  livePrice,
  changePct,
  onSelect,
}: AiTradePanelProps) {
  const group = useAppStore((s) => s.aiTradeGroup);
  const resumable = useAppStore((s) => s.aiTradeResumable);
  const syncing = useAppStore((s) => s.aiTradeSyncState) === "unknown";
  // 倒计时每秒 tick（hooks 必须在早退**之前**声明：条件调用 hook 会直接违反 hooks 规则）。
  const [nowTs, setNowTs] = useState(() => Math.floor(Date.now() / 1000));
  const hasLifecycle = !!group?.lifecycle;
  useEffect(() => {
    if (!hasLifecycle) return;
    const timer = setInterval(() => setNowTs(Math.floor(Date.now() / 1000)), 1_000);
    return () => clearInterval(timer);
  }, [hasLifecycle]);

  // 冷续跑卡（ai-trade-pause-resume §1.3 / §3.9.2）：进程重启后内存里没有会话，但磁盘上仍有一份
  // 可续跑契约。**与热态互斥**（后端 G-9 冻结），故这条早退恰好把两者切开。
  if (!group) return resumable ? <ResumableCard card={resumable} /> : null;

  const members = Object.values(group.members);
  const aggregatePnl = members.reduce((sum, m) => sum + (m.session?.sessionPnlUsd ?? 0), 0);
  // 净额徽标的门控**只认后端下发的口径标记**，绝不据配置或猜测推断：口径是会话级静态量，
  // 前端说「已扣手续费」而后端其实是毛额，比不显示徽标危险得多。
  const netBasis = members.some((m) => m.position?.costAwareness?.netBasis === true);
  // L-4：**生命周期只有一个推导点**。此前这里有两行自拼的判据（`group.state === "stopped" ||
  // members.every(...)`），与 `AiTradeToggle` 走的 `resolveAiTradeLifecycle` 口径不同 —— 于是
  // 面板徽标说「已暂停 / 已停止」，头部开关同时还是开的，用户对着两个互相矛盾的控件做真实资金
  // 决策（D-5）。两行判据本轮**删除**，改调唯一入口。
  const lifecycle = resolveAiTradeLifecycle(group, null);
  const armed = resolveAiTradeArmed(group);
  const badge = describeAiTradeLifecycleBadge(lifecycle, armed, syncing);
  const stopped = lifecycle === "stopped";
  const paused = lifecycle === "paused";
  const countdown = formatNextWake(group.lifecycle, lifecycle, nowTs);

  return (
    <div className="flex flex-col gap-2 border-b border-[var(--border)] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold text-[var(--text)]">AI 自动交易</span>
          <Badge tone={badge.tone}>
            <span data-testid="ai-trade-panel-badge">{badge.label}</span>
          </Badge>
          {/* 「下一次决策 03:12」：用户看到「运行中」的同时看到 Agent 下一次思考还有多久，
              这才叫恢复到了「Agent 执行交易的状态」（§1.3 目标 2）。 */}
          {countdown && (
            <span
              data-testid="ai-trade-panel-next-wake"
              className="rounded border border-[var(--border)] px-1 py-px text-[9px] tabular text-[var(--muted)]"
              title="下一次 Agent 决策的预计时刻（以服务端时钟为锚点）"
            >
              {countdown}
            </span>
          )}
          {members.length > 1 && (
            <span className="text-[10px] text-[var(--muted)]">{members.length} 个标的</span>
          )}
        </div>
        <span className="flex items-center gap-1.5">
          {netBasis && (
            <span
              title={COST_BASIS_HINT}
              data-testid="ai-trade-net-basis"
              className="rounded border border-[var(--border)] px-1 py-px text-[9px] text-[var(--muted)]"
            >
              {describeCostBasis("net")}
            </span>
          )}
          <span
            className={
              aggregatePnl >= 0
                ? "tabular text-xs font-medium text-[var(--long-ink)]"
                : "tabular text-xs font-medium text-[var(--short-ink)]"
            }
          >
            {aggregatePnl >= 0 ? "+" : ""}
            {fmtMoney(aggregatePnl)}
          </span>
        </span>
      </div>

      {/* 暂停横幅排在最前：暂停期「每轮都没有分析、也从不开单」是**预期行为**，先说清这一条，
          下面那条可交易性红条才不会被读成「AI 坏了」的第二个证据。 */}
      {paused && <PausedBanner group={group} members={members} />}

      {/* 终态留痕（F-5）：分组在后端已经消失，但成员如实 `stopped` ⇒ 保留一张「已停止」卡。
          会话自行终止（会话止损 / 行情中断强平）是用户**必须**知道的事，面板整块消失等于没说。 */}
      {stopped && <StoppedCard members={members} />}

      {/* 「运行中但没真的在跑」（L-2）：徽标已经降级，横幅**逐标的**说明为什么。
          绝不用整组与门把还在正常交易的那个也说成停了（[评审 R1-7]）。 */}
      {lifecycle === "running" && !armed.unavailable && !armed.armed && (
        <NotArmedBanner rows={armed.rows} total={armed.membersTotal} />
      )}

      {/* 可交易性红条 + 长期记忆停摆徽标（tradability-and-open-funnel 轴 A / 轴 D）。
          放在纪律条**之上**：当探针判定「这个周期几乎开不出单」时，那是用户最该先看到的一句话
          ——否则「开着、在跑、每轮都有分析、就是从不开单」只会被读成「AI 坏了」。 */}
      <TradabilityStrip members={members} />
      <SummaryStalledStrip members={members} />

      <DisciplineStrip group={group} />

      <div className="flex flex-col gap-1">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--muted)]">
          自动交易标的
        </span>
        {/* 离场卡**必须在 MemberRow 之外**（exit-priority §4.6.3）：MemberRow 本体是一个
            `<button>`，而【立即离场】也是按钮——按钮不能嵌套按钮。未武装 ⇒ 整块不渲染。 */}
        {members.map((m) => (
          <div key={m.instId} className="flex flex-col gap-1">
            <MemberRow member={m} rows={rows} active={active} livePrice={livePrice}
                       changePct={changePct} onSelect={onSelect} />
            <AiTradeExitCard groupId={group.groupId} member={m} />
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 冷续跑卡（ai-trade-pause-resume §1.3 / §3.9.2 的冷态文案）。
 *
 * **措辞的可信度必须与「此刻真的知道多少」对齐**（[P2-9]）：进程刚起、还没向交易所发过任何请求，
 * 故这里**绝不能**说「交易所侧止损仍生效」。关机前那次核实只能以**过去时**呈现；那次核实缺失或
 * 不可得时，整句改成「关机前未能确认是否有交易所侧兜底」——宁可说不知道，也不编一句安心话。
 *
 * 动作（继续 / 结束）在 AI 面板头部的控件里，与本卡的分工同既有「面板呈现、控件动作」惯例。
 */
function ResumableCard({ card }: { card: AiTradeResumableGroup }) {
  const holding = card.members.filter((m) => !!m.position || m.positionUnknown);
  return (
    <div
      className="flex flex-col gap-1.5 border-b border-[var(--border)] px-3 py-2.5"
      data-testid="ai-trade-resumable-card"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-semibold text-[var(--text)]">已暂停的 AI 自动交易</span>
        <Badge tone="accent">可继续</Badge>
      </div>
      <span className="text-[10px] leading-relaxed text-[var(--muted)]">
        {card.members.map((m) => m.instId).join("、")} · {card.bar} · 预算{" "}
        {fmtMoney(card.totalBudgetUsd)} · 已暂停 {card.offlineText}
        {card.pauseReasonText ? ` · ${card.pauseReasonText}` : ""}
        {card.resumedCount > 0 ? ` · 已续跑 ${card.resumedCount} 次` : ""}
      </span>
      {holding.length > 0 ? (
        holding.map((m) => (
          <span key={m.instId} className="text-[10px] leading-relaxed text-[var(--muted)]">
            {m.instId}：
            {m.positionUnknown
              ? "关机前未能读到持仓，继续前会重新向交易所核实。"
              : `仍持有${m.position?.side === "long" ? "多头" : "空头"} ${fmtNum(
                  Number(m.position?.size ?? 0),
                )} 张。`}
            {describeBackstopAtPause(m.backstopAtPause, card.offlineText)}
          </span>
        ))
      ) : (
        <span className="text-[10px] text-[var(--muted)]">暂停时无持仓。</span>
      )}
      <span className="text-[10px] text-[var(--muted)]">
        点右侧「AI 自动交易」旁的「▶ 继续」核对持仓后接着跑；也可以直接结束并归档。
      </span>
    </div>
  );
}

/** 关机前那次兜底核实的**过去时**描述；缺失 / 不可得 ⇒ 明说不知道，绝不编一句安心话。 */
function describeBackstopAtPause(
  backstop: AiTradeBackstopAtPause | null | undefined,
  offlineText: string,
): string {
  if (!backstop) return "关机前未能确认是否有交易所侧兜底。";
  if (!backstop.covered) return "关机前核实的结果是：没有任何持久化兜底。";
  const px = backstop.stopPx;
  return `关机前最后一次核实（${offlineText}前）交易所侧止损为 ${px == null ? "—" : fmtPrice(px)}；当前状态需点「继续」后向交易所核实。`;
}

/**
 * 暂停期常驻横幅（ai-trade-pause-resume §3.9.2）。
 *
 * **常驻，不是 toast**：暂停可能持续一整夜，一条会消失的提示等于没有提示；而「暂停」很容易被
 * 误读成「AI 坏了」，横幅要把「哪些东西还在跑」明确写出来。
 *
 * 措辞的可信度必须与「此刻真的知道多少」对齐（诚实不变式 I-4 / [P2-9]）：这里是**热暂停**
 * （会话在内存里、盯市与兜底核实照常跑），故可以说「仍挂着」；冷续跑卡在另一处，它只能说
 * 「关机前最后一次核实是…」——那是过去时，绝不能声称当下仍生效。
 */
function PausedBanner({
  group,
  members,
}: {
  group: AiTradeGroupState;
  members: AiTradeGroupMemberState[];
}) {
  const pausedTs = group.pausedTs ?? null;
  const [nowTs, setNowTs] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const timer = setInterval(() => setNowTs(Math.floor(Date.now() / 1000)), 30_000);
    return () => clearInterval(timer);
  }, []);
  const holding = members.filter((m) => !!m.position);
  const reason = group.pauseReason ? AI_TRADE_PAUSE_REASON_TEXT[group.pauseReason] : null;

  return (
    <div
      className="flex flex-col gap-1 rounded border border-[var(--accent)] bg-[var(--accent-soft)] px-2 py-1.5"
      data-testid="ai-trade-paused-banner"
    >
      <span className="text-[10px] font-medium leading-relaxed text-[var(--accent-ink)]">
        已暂停：不再开新仓；盯市、保护性平仓、会话止盈止损与交易所侧兜底核实照常运行。
        {pausedTs ? ` 已暂停 ${formatPausedFor(pausedTs, nowTs)}。` : ""}
        {reason ? ` （${reason}）` : ""}
      </span>
      {holding.length > 0 && (
        <span className="text-[10px] leading-relaxed text-[var(--muted)]">
          仍持有：
          {holding
            .map((m) => `${m.instId} ${m.position?.side === "long" ? "多头" : "空头"}`)
            .join("、")}
          。关闭软件后由交易所侧止损兜底；重新打开并联网后点「继续」即可接着跑。
        </span>
      )}
    </div>
  );
}

/**
 * 「未武装」横幅（ai-trade-lifecycle-truth F-6 / L-2）。
 *
 * `running` 却没真的在跑时，**逐标的**说明原因。文案一律取后端下发的 `reasonText`（判定性说明
 * 单一真源，§3.10）；前端只负责把标的名拼在前面，且**只在多标的时**拼 —— 单标的分组里那个前缀
 * 是纯噪音。
 *
 * 用 `--warn` / `--warn-soft` 而不是 `Badge tone`：这里是一个普通 div，可以直接用设计系统那对
 * 警告色；而 `components/ui/index.tsx` 是全仓共用的 kit，本轮不动它（§1.4）。
 * 图标 + 文字双通道，绝不靠颜色单通道传达（色觉障碍友好，与 globals.css 的既有纪律同源）。
 */
function NotArmedBanner({ rows, total }: { rows: AiTradeNotArmedRow[]; total: number }) {
  if (rows.length === 0) return null;
  return (
    <div
      className="flex flex-col gap-1 rounded border border-[var(--warn)] bg-[var(--warn-soft)] px-2 py-1.5"
      data-testid="ai-trade-not-armed-banner"
      role="alert"
    >
      <span className="text-[10px] font-medium leading-relaxed text-[var(--warn)]">
        ⚠ Agent 暂时不会做出新的决策
        {total > 1 ? `（${rows.length}/${total} 个标的未武装，其余照常交易）` : ""}
      </span>
      {rows.map((row) => (
        <span key={row.instId} className="text-[10px] leading-relaxed text-[var(--warn)]">
          {total > 1 ? `${row.instId}：` : ""}
          {row.reasonText}
        </span>
      ))}
    </div>
  );
}

/**
 * 已停止卡（ai-trade-lifecycle-truth F-5：终态留痕）。
 *
 * 后端此刻已经清了组（B-1），若面板跟着整块消失，「你的会话在夜里自己止损平仓并结束了」这件事
 * 就再也没人告诉用户。理由与 `end` 的孤儿仓提示同源。清理动作在头部控制条的「知道了」上
 * （面板呈现、控件动作，与既有分工一致）。
 */
function StoppedCard({ members }: { members: AiTradeGroupMemberState[] }) {
  return (
    <div
      className="flex flex-col gap-1 rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5"
      data-testid="ai-trade-stopped-card"
    >
      <span className="text-[10px] font-medium leading-relaxed text-[var(--text)]">
        这次 AI 自动交易已经结束，Agent 不会再做任何决策。
      </span>
      {members.map((m) => (
        <span key={m.instId} className="text-[10px] leading-relaxed text-[var(--muted)]">
          {m.instId}：{describeStopReason(m.session?.stopReason)}
          {typeof m.session?.sessionPnlUsd === "number"
            ? ` · 本次会话 ${m.session.sessionPnlUsd >= 0 ? "+" : ""}${fmtMoney(m.session.sessionPnlUsd)}`
            : ""}
        </span>
      ))}
      <span className="text-[10px] leading-relaxed text-[var(--muted)]">
        若交易所上仍有持仓，它从此不再由任何自动化管理，请到下方实盘面板核对。
      </span>
    </div>
  );
}

/**
 * 可交易性红条（tradability-and-open-funnel 轴 A · §3.1.3 落点三）。
 *
 * **只在 `infeasible` 且本会话一单未成交时**出现——那正是「开着、在跑、就是从不开单」的现场。
 * 已经成交过的会话不该被一条红条打扰（探针是启动断面，行情会变）。逐成员各判一次：多标的分组里
 * 只看第一个成员会漏报后面的标的。
 */
function TradabilityStrip({ members }: { members: AiTradeGroupMemberState[] }) {
  const rows = members
    .map((m) => ({
      instId: m.instId,
      view: describeTradability(m.session?.tradability),
      filled: m.session?.openFunnel?.filled ?? 0,
    }))
    .filter((row) => row.view?.level === "infeasible" && row.filled === 0);
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col gap-1" data-testid="ai-tradability-strip">
      {rows.map((row) => (
        <p
          key={row.instId}
          className="rounded border border-[var(--short)] px-2 py-1 text-[10px] leading-relaxed text-[var(--short-ink)]"
        >
          <strong>{members.length > 1 ? `${row.instId} · ` : ""}当前周期几乎开不出单</strong>
          ：{row.view?.headline}
          {row.view?.advice ? ` ${row.view.advice}` : ""}
        </p>
      ))}
    </div>
  );
}

/**
 * 「长期记忆已停摆」徽标（轴 D · D-6）。
 *
 * 合入前摘要失败在全仓**没有任何呈现落点**（`_status_payload` 只有 `roundsSinceSummary`），
 * 而实测出现过 4 条连续失败且诊断串全为空。文案必须说明**后果**——只报「失败」不报后果，
 * 用户无法判断要不要停会话。
 */
function SummaryStalledStrip({ members }: { members: AiTradeGroupMemberState[] }) {
  const stalled = members.find((m) => m.session?.summaryHealth?.stalled);
  const health = stalled?.session?.summaryHealth;
  if (!health) return null;
  return (
    <p
      className="rounded border border-[var(--accent)] px-2 py-1 text-[10px] leading-relaxed text-[var(--accent-ink)]"
      data-testid="ai-summary-stalled-badge"
      title={health.lastError ?? undefined}
    >
      <strong>长期记忆已停摆</strong>（连续 {health.failures} 次摘要失败）：
      模型仍在按当前行情决策，但会逐渐忘记更早的执行历程。
    </p>
  );
}

/**
 * 纪律条（discipline-preset 轴 B 的紧凑落点 + 轴 C 的裸仓警示）。
 *
 * 为什么放在主面板顶部而不是只放统计弹窗：本轮把纪律层默认装配起来后，用户看到的第一个变化是
 * 「AI 开单变少」。若「哪些纪律正在生效」只存在于一个要主动点开的弹窗里，这个变化只会被读成
 * 「AI 坏了」——**后端算了但用户看不见 = 死数据**，这正是本仓库反复出现的缺陷形状。
 *
 * 红色态**由 `disciplines.exchangeBackstop` 行驱动**（[P0-2]），不是由任何 `error` 事件驱动：
 * `useAiTradeGroupStream.routeMemberEvent` 根本没有 `error` 分支，那条通道是个 /dev/null。
 */
function DisciplineStrip({ group }: { group: AiTradeGroupState }) {
  const [open, setOpen] = useState(false);
  const members = Object.values(group.members);
  // 成员级 status 优先：`exchangeBackstop` 是唯一逐成员分叉的一行，分组回执只做兜底。
  const digest = memberDisciplines(members[0]?.session?.disciplines, group.discipline);
  const rows = digest?.rows ?? [];
  const summary = summarizeDisciplines(rows);
  // 裸仓可能发生在**任意**成员上，故逐成员各判一次（只看第一个成员会漏报后面的标的）。
  const naked = members
    .map((m) => ({
      instId: m.instId,
      row: disciplineRow(m.session?.disciplines?.rows ?? rows, "exchangeBackstop"),
    }))
    .filter((entry) => entry.row?.tone === "danger");
  if (!summary.label) return null;   // 老后端 / 还没数据 ⇒ 整条不占位

  return (
    <div className="flex flex-col gap-1" data-testid="ai-discipline-strip">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-left transition-colors hover:bg-[var(--surface-2)]"
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="text-[10px] text-[var(--muted)]">交易纪律</span>
          {group.discipline?.presetLabel && (
            <Badge tone="neutral">{group.discipline.presetLabel}</Badge>
          )}
          <span className="tabular text-[10px] text-[var(--muted)]">{summary.label}</span>
          {summary.warnings > 0 && (
            <span className="text-[10px] text-[var(--accent-ink)]">{summary.warnings} 项需注意</span>
          )}
        </span>
        <span className="shrink-0 text-[10px] text-[var(--muted)]">{open ? "收起" : "展开"}</span>
      </button>

      {naked.map((entry) => (
        <NakedBackstopAlert
          key={entry.instId}
          groupId={group.groupId}
          instId={entry.instId}
          showInstId={members.length > 1}
          note={entry.row?.note ?? null}
        />
      ))}

      {digest?.hint && (
        <p
          className="rounded border border-[var(--short)] px-2 py-1 text-[10px] leading-relaxed text-[var(--short-ink)]"
          data-testid="ai-discipline-strip-hint"
        >
          {digest.hint}
        </p>
      )}

      {open && (
        <div className="flex flex-col gap-0.5 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1">
          {rows.map((row) => (
            <CompactDisciplineRow key={row.key} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}

function CompactDisciplineRow({ row }: { row: AiTradeDisciplineRow }) {
  const color =
    row.tone === "danger"
      ? "var(--short)"
      : row.tone === "warn"
        ? "var(--accent)"
        : row.armed
          ? "var(--long)"
          : "var(--muted)";
  return (
    <span className="flex items-center justify-between gap-2 text-[10px]">
      <span className="truncate" style={{ color }} title={row.note ?? undefined}>
        {row.label}
      </span>
      <span className="tabular shrink-0 text-[var(--muted)]">
        {row.armed ? (row.threshold ?? "已启用") : "未生效"}
      </span>
    </span>
  );
}

/**
 * 裸仓警示条（轴 C）：高显著度、常驻、带「立即补挂」直连既有 `repairBackstop` 端点。
 *
 * 三态按钮沿用 `AiTradeStatsLive` 已有范式（含 502 的中文原因在前）。文案只复述后端那一行的
 * `note`——两处各写一遍就是两份口径，而这一条是安全告警，措辞失准的代价最大。
 */
function NakedBackstopAlert({
  groupId,
  instId,
  showInstId,
  note,
}: {
  groupId: string;
  instId: string;
  showInstId: boolean;
  note: string | null;
}) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const repair = useCallback(() => {
    setPending(true);
    setResult(null);
    aiTradeApi
      .repairBackstop(groupId, instId)
      .then(() => setResult("补挂请求已发出，正在向交易所核实"))
      .catch((err: unknown) => setResult(repairErrorText(err)))
      .finally(() => setPending(false));
  }, [groupId, instId]);

  return (
    <div
      className="flex flex-col gap-1 rounded border border-[var(--short)] bg-[var(--surface-2)] px-2 py-1"
      data-testid="ai-discipline-naked-alert"
    >
      <span className="text-[10px] font-medium leading-relaxed text-[var(--short-ink)]">
        {showInstId ? `${instId} · ` : ""}
        {note ?? "当前持仓在交易所侧没有兜底止损"}
      </span>
      <span className="flex items-center gap-2">
        <button
          type="button"
          onClick={repair}
          disabled={pending}
          className="rounded border border-[var(--short)] px-1.5 py-px text-[10px] text-[var(--short-ink)] transition-colors hover:bg-[var(--short)] hover:text-[var(--bg)] disabled:opacity-50"
        >
          {pending ? "补挂中…" : "立即补挂"}
        </button>
        {result && <span className="truncate text-[10px] text-[var(--muted)]">{result}</span>}
      </span>
    </div>
  );
}

/** 补挂失败的人话（绝不把裸码或原始异常抛给用户；交易所拒单时带上 sCode 供后台核对）。 */
function repairErrorText(err: unknown): string {
  if (err instanceof ApiRequestError) {
    return err.code ? `${err.message}（${err.code}）` : err.message;
  }
  return "补挂请求失败，请稍后重试";
}

function MemberRow({
  member,
  rows,
  active,
  livePrice,
  changePct,
  onSelect,
}: {
  member: AiTradeGroupMemberState;
  rows: Instrument[];
  active: string;
  livePrice?: number | null;
  changePct?: number | null;
  onSelect: (instId: string) => void;
}) {
  const { instId } = member;
  const session = member.session;
  const positionSide = session?.positionSide ?? "flat";
  // 「当前挂单」取最新一轮决策随附的挂单断面（后端 _emit_decision 与决策 prompt 同源 resting_payload）。
  // 无挂单 / 跳过轮（该事件不带此段）→ 整行省略，绝不据 orderType 反推「挂单还在」（可能已成交/撤销）。
  const resting = session?.lastDecision?.restingOrder ?? null;
  // 利润保护行的渲染门控**必须**是「确实还持仓」且「快照存在」二者同时成立：`member.position` 在平仓后
  // 不会被清空（后端 `_emit_position` 无仓时不发事件、`store.setAiTradeMemberPosition` 也无复位路径），
  // 只判快照存在会在空仓后继续显示一个已不存在持仓的峰值与退出线。
  const guard = positionSide !== "flat" ? (member.position?.profitGuard ?? null) : null;
  // 标的行情解析：命中自选行则富化（且 active 标的用 WS 实时价覆盖快照）；未收藏则优雅降级不造价。
  const inst = rows.find((r) => r.instId === instId) ?? null;
  const isActive = instId === active;
  const price = inst ? (isActive ? (livePrice ?? inst.last ?? null) : (inst.last ?? null)) : null;
  const pct = inst ? (isActive ? (changePct ?? inst.changePct ?? null) : (inst.changePct ?? null)) : null;

  return (
    <button
      type="button"
      onClick={() => onSelect(instId)}
      aria-current={isActive}
      className="flex w-full items-center justify-between gap-2 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-left transition-colors hover:bg-[var(--surface-2)] aria-[current=true]:border-[var(--accent)]"
    >
      <span className="flex min-w-0 flex-col">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium text-[var(--text)]">{instId}</span>
          {session?.bar && <span className="shrink-0 text-[10px] text-[var(--muted)]">{session.bar}</span>}
        </span>
        <span className="truncate text-[10px] text-[var(--muted)]">
          预算 {fmtNum(session?.budgetUsd ?? 0)} USDT · {session?.leverage ?? 0}× · {member.allocationPct}%
        </span>
        {resting && (
          <span className="tabular truncate text-[10px] text-[var(--accent-ink)]">
            当前挂单 {fmtPrice(resting.px)} · {fmtNum(resting.sz, 0)} 张 · 剩余 {resting.expiresInS}s
          </span>
        )}
        {/* 会话状态行（lifecycle-truth F-4）：行情断连 / 已恢复 / 补根结果 / WS 错误帧。
            后端**早就在下发它**（`_check_staleness` 与 `SessionLink._push_message` 都推
            `{...status, message}`），但全仓一直没有读点 —— 「wire 里有事实、用户那里没有」
            的又一处，本轮补上。 */}
        {session?.message && (
          <span
            data-testid="ai-trade-member-message"
            className="truncate text-[10px] text-[var(--warn)]"
            title={session.message}
          >
            {session.message}
          </span>
        )}
        <HorizonChip member={member} />
        <TurnoverChip member={member} />
        <BreakEvenRow member={member} />
        <ProfitGuardRow guard={guard} />
        <ProfitGuardCooldownChip member={member} />
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <Badge tone={positionTone(positionSide)}>{describePosition(positionSide)}</Badge>
        {price != null && (
          <span className="flex flex-col items-end">
            <span className="text-[11px] tabular text-[var(--text)]">{fmtPrice(price)}</span>
            {pct != null && (
              <span className="text-[10px] tabular" style={{ color: colorForReturn(pct) }}>
                {fmtPct(pct)}
              </span>
            )}
          </span>
        )}
      </span>
    </button>
  );
}

/**
 * 交易周期 chip（ai-trade-holding-horizon-modes §2.11 展示落点之一）。
 *
 * 门控**只判快照存在**，刻意**不**叠加 `positionSide !== "flat"`：本层的空仓态是一条有信息量的
 * 消息（「本层不约束开仓」），把它一并吞掉正是 `ai-trade-profit-protection` 终审 RV-2 的教训——
 * 新增边缘态必须验渲染门控没排除掉该态。快照由 status / decision 事件**整对象透传**（AI-trade 的
 * position/status 是整对象合并，不存在 `edgeSource` 那种逐键手抄吞新键的问题）。
 *
 * `released` 用 `--short` 色：此刻锁是开的、AI 可以随时平仓，用常规灰会让用户以为限制还在。
 */
function HorizonChip({ member }: { member: AiTradeGroupMemberState }) {
  const wire = member.session?.horizon ?? null;
  const text = describeHorizonStatus(wire);
  if (!text) return null;
  return (
    <span
      className={
        wire?.released
          ? "truncate text-[10px] text-[var(--short-ink)]"
          : "truncate text-[10px] text-[var(--muted)]"
      }
      data-testid="ai-trade-horizon-chip"
    >
      {text}
    </span>
  );
}

/**
 * 换手纪律与兜底 chip（turnover-budget §2.4.3）：**只显示最紧要的一格**。
 *
 * 优先级 `mismatch` > 覆盖不足 > 冷却中 > 预算耗尽；**正常态返回 null ⇒ 整行不占位**（R-7：
 * 本行的价值只在异常时，常态多一行只会稀释上面那条周期 chip）。门控与上面同款——只判快照存在，
 * 绝不叠加 `positionSide !== "flat"`：空仓恰是换手闸唯一真正会拦人的状态。
 */
function TurnoverChip({ member }: { member: AiTradeGroupMemberState }) {
  const wire = member.session?.horizon ?? null;
  const chip = describeTurnoverChip(wire?.turnover, wire?.backstop);
  if (!chip) return null;
  const color = chip.tone === "short" ? "var(--short)" : "var(--accent)";
  return (
    <span
      className="truncate text-[10px]"
      style={{ color }}
      data-testid="ai-trade-turnover-chip"
    >
      {chip.label}
    </span>
  );
}

/**
 * 盈亏平衡价行（ai-trade-cost-truth-and-net-pnl §3.5 F-4）。
 *
 * 只在**确实还持仓**且后端下发了成本证据时显示。平衡价**与杠杆无关**（杠杆只放大保证金收益率，
 * 不改变回本所需的价格距离）——这正是用户最容易算错的一处，故它必须由后端算好下发，前端不重算。
 */
function BreakEvenRow({ member }: { member: AiTradeGroupMemberState }) {
  // 门控与 ProfitGuardRow 逐字同款：`member.position` 在平仓后不会被清空（后端无仓时不发事件、
  // store 也无复位路径），只判快照存在会在空仓后继续显示一条已不存在持仓的平衡价。
  const positionSide = member.session?.positionSide ?? "flat";
  const cost = positionSide !== "flat" ? member.position?.costAwareness : null;
  if (!cost || cost.breakEvenPx == null) return null;
  const gap = cost.breakEvenGapPct;
  return (
    <span className="tabular truncate text-[10px] text-[var(--muted)]" data-testid="ai-break-even">
      盈亏平衡价 {fmtPrice(cost.breakEvenPx)}
      {gap == null ? "" : gap <= 0 ? "（已覆盖成本）" : `（还差 ${(gap * 100).toFixed(3)}%）`}
      {" ｜ 往返成本 "}
      {cost.roundTripBps} bps
    </span>
  );
}

/**
 * 利润保护行（ai-trade-profit-protection §六.4）：峰值浮盈 / 已回吐% / 有效退出线 + 武装徽标。
 *
 * `unavailable` 只出一句「暂不可用」，**绝不显示退出线**——一条算得出来却不作数的价位比没有更危险。
 * 峰值浮盈为**本地派生**口径（不含手续费 / 资金费），与持仓行的交易所 upl 并列而非互相冒充。
 */
function ProfitGuardRow({ guard }: { guard: ProfitGuardSnapshot | null }) {
  const view = describeProfitGuardStatus(guard);
  if (!guard || !view) return null;
  if (guard.status === "unavailable") {
    return (
      <span className="truncate text-[10px] text-[var(--short-ink)]" data-testid="ai-profit-guard">
        {view.label}（{describeProfitGuardFallback(guard)}）
      </span>
    );
  }
  const peak = guard.peakUnrealizedPnlUsd;
  const giveback = guard.givebackRatio;
  const exitPx = guard.effectiveExitPx;
  // 「这条线被谁定住」必须写出来：自适应之后用户已无法心算出这条线，看不懂就不会信它（R-11）。
  const trail = describeProfitGuardTrail(guard);
  // 承诺退出线（commitment C4）：模型自己许下的第三条保护线；「当前由它定住」只读后端归因（[P1-3]）。
  const committed = describeCommittedExit(guard);
  return (
    <span className="tabular truncate text-[10px] text-[var(--muted)]" data-testid="ai-profit-guard">
      峰值 {peak == null ? "—" : `${peak >= 0 ? "+" : ""}${fmtMoney(peak)}`}
      {" ｜ 已回吐 "}
      {giveback == null ? "—" : `${(giveback * 100).toFixed(1)}%`}
      {" ｜ 退出线 "}
      {exitPx == null ? "未武装" : `${fmtPrice(exitPx)}（${view.label.replace("利润保护 · ", "")}）`}
      {trail && <span className="block truncate">{trail}</span>}
      {committed && (
        <span className="block truncate" data-testid="ai-profit-guard-committed">
          {committed}
          {isEffectiveExitCommitted(guard) ? "（当前由它定住）" : ""}
        </span>
      )}
    </span>
  );
}

/**
 * 保护性平仓冷却 chip（adaptive §4.2）。
 *
 * 门控**必须**是 `member.profitGuardCooldown != null`，**不得**挂在 `member.position` 之下：冷却期内
 * 按定义已无持仓（position 恒 null），挂上去这枚 chip 永远不会出现。冷却结束由后端主动补推一次
 * status（该键为 null）摘掉它——前端不据 remainingSeconds 自行倒计时判真伪（两个时钟必然漂移）。
 */
function ProfitGuardCooldownChip({ member }: { member: AiTradeGroupMemberState }) {
  const text = describeProfitGuardCooldown(member.profitGuardCooldown);
  if (!text) return null;
  return (
    <span className="truncate text-[10px] text-[var(--accent-ink)]" data-testid="ai-profit-cooldown">
      {text}
    </span>
  );
}

function describePosition(side: string): string {
  if (side === "long") return "多";
  if (side === "short") return "空";
  return "空仓";
}

function positionTone(side: string): "long" | "short" | "neutral" {
  if (side === "long") return "long";
  if (side === "short") return "short";
  return "neutral";
}
