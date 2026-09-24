"use client";

import { Badge } from "@/components/ui";
import AiTradeProfitLockRow from "./AiTradeProfitLockRow";
import {
  describeCommittedExit,
  describeProfitGuardCooldown,
  describeProfitGuardFallback,
  describeProfitGuardStatus,
  describeProfitGuardTrail,
  isEffectiveExitCommitted,
} from "@/lib/aiTrade";
import { describeHorizonStatus } from "@/lib/aiTradeHorizon";
import { currentAiTradeEpoch, isAiTradeNoticeLive } from "@/lib/aiTradeNotice";
import { describeTurnoverChip } from "@/lib/aiTradeTurnover";
import { describeIntegrity } from "@/lib/aiTradeIntegrity";
import { colorForReturn, fmtMoney, fmtNum, fmtPct, fmtPrice } from "@/lib/format";
import type { AiTradeGroupMemberState, ProfitGuardSnapshot } from "@/lib/aiTradeTypes";
import { useAppStore } from "@/lib/store";
import type { Instrument } from "@/lib/types";

// AI 自动交易「标的行」（ai-trade-market-intel §3.8.1 的**强制前置步骤**）。
//
// 为什么它住在这里而不是 AiTradePanel.tsx：搬运前 AiTradePanel.tsx 实读 799 行，而 CLAUDE.md 的
// 阈值是 800 —— 情报特性的第一行代码就会越线。本文件是一次**纯搬运**：函数体逐字节取自
// AiTradePanel.tsx，只把 `MemberRow` 改名为默认导出的 `AiTradeMemberRow`。
//
// **本组件的根节点是一个 `<button>`**（离场卡与情报按钮因此都被刻意放在它**外面**——按钮不能
// 嵌套按钮）。任何往这里加动作按钮的改动都会在打包后变成一处无法点击的 UI，
// `aiTradePanelNoNestedButton.source.test.ts` 冻结这条约束。


export default function AiTradeMemberRow({
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
  // 状态行的判活纪元（notice-state-sync §4.5）：三个订阅源缺一不可，否则「暂停 → 继续之后那条
  // 橙色状态行消失」在某一条路径上不会重算。`member_status` 的 `ttlMs` 为 null，故无需秒表。
  const noticeGroup = useAppStore((s) => s.aiTradeGroup);
  const noticeResumable = useAppStore((s) => s.aiTradeResumable);
  const noticeReconciledAtMs = useAppStore((s) => s.aiTradeReconciledAtMs);
  // **缺席即渲染**（§6.2）：只有**盖过戳、且戳已过期**的才隐藏。老快照、既有直接构造成员对象的
  // 用例、以及 `lifecycle` 总闸关时的「故意不盖戳」，三者都靠这条极性逐字回退到本轮之前的行为。
  const messageLive =
    member.messageEpoch == null ||
    isAiTradeNoticeLive(
      { kind: "member_status", text: session?.message ?? "", epoch: member.messageEpoch, ts: 0 },
      currentAiTradeEpoch(noticeGroup, noticeResumable, noticeReconciledAtMs),
      0,
    );
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
      className="flex w-full flex-wrap items-center justify-between gap-x-2 gap-y-0.5 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-left transition-colors hover:bg-[var(--surface-2)] aria-[current=true]:border-[var(--accent)]"
    >
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs font-medium text-[var(--text)]" title={instId}>{instId}</span>
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
            的又一处，上一轮补上。
            notice-state-sync §4.5：这条回声**没有任何复位点**（`_status_payload()` 不含
            `message` 键，浅合并清不掉；行情自愈的 L4 告警又「只出声一次」），于是它会跨过一次
            暂停/继续往返继续说旧话。改法是写点盖纪元戳、读点过同一个谓词。
            清掉一条过期回声是安全的，理由写在 store 的 `noteAiTradeMemberMessage` 上。 */}
        {session?.message && messageLive && (
          <span
            data-testid="ai-trade-member-message"
            className="truncate text-[10px] text-[var(--warn)]"
            title={session.message}
          >
            {session.message}
          </span>
        )}
        <SettlingChip member={member} />
        <OrphanChip member={member} />
        <HorizonChip member={member} />
        <TurnoverChip member={member} />
        <BreakEvenRow member={member} />
        <AiTradeProfitLockRow member={member} />
        <ProfitGuardRow guard={guard} />
        <ProfitGuardCooldownChip member={member} />
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <Badge tone={positionTone(positionSide)}>{describePosition(positionSide)}</Badge>
        {price != null && (
          <span className="flex flex-col items-end">
            <span className="max-w-[88px] truncate text-[11px] tabular text-[var(--text)]"
              title={`${price.toLocaleString("en-US", { maximumSignificantDigits: 21, useGrouping: false })} USDT`}>
              {price !== 0 && Math.abs(price) < .0001
                ? price.toExponential(3) : fmtPrice(price)}
            </span>
            {pct != null && (
              <span className="text-[10px] tabular" style={{ color: colorForReturn(pct) }}>
                {fmtPct(pct)}
              </span>
            )}
          </span>
        )}
      </span>
      <span className="min-w-0 basis-full truncate text-[10px] text-[var(--muted)]"
        data-testid="ai-member-integrity"
        title={describeIntegrity(session?.integrity) +
          (session?.integrity?.asOfTs != null
            ? ` · 更新 ${new Date(session.integrity.asOfTs * 1000).toLocaleTimeString()}`
            : "")}
      >
        {describeIntegrity(session?.integrity)}
      </span>
    </button>
  );
}

/**
 * 结算中 chip（fill-truth-and-effective-controls 轴 A，§十三 Q3 的裁定）。
 *
 * **这枚芯片是必需的而不是可选的。** `settling` 期间一切出站平仓被短路（不变式 I-2b：仓位在
 * 交易所侧已经不在了，此刻再发一张平仓单只会换回一个 `LIVE_NO_POSITION`，而那条良性分支会
 * 按标记价抢先落一次账 ⇒ 与随后的结算落账**双记已实现盈亏**）。于是用户点「手动平仓」会得到
 * 一句「本轮不发平仓单」——如果这一行还写着「持有中」，那句话就变成了一个无法解释的拒绝。
 *
 * 未装配 / 无未决态 ⇒ 后端整键省略 ⇒ 不渲染。
 */
function SettlingChip({ member }: { member: AiTradeGroupMemberState }) {
  const wire = member.session?.settling ?? null;
  if (!wire?.active) return null;
  return (
    <span
      className="truncate text-[10px] text-[var(--accent-ink)]"
      data-testid="ai-trade-settling-chip"
      title={`侦测于 ${new Date(wire.detectTs * 1000).toLocaleTimeString()}；` +
        "期间本会话不会再对它发出任何平仓单"}
    >
      结算中 · 正在取回交易所成交价（已等 {wire.waitedS}s）
    </span>
  );
}

/**
 * 孤儿持仓 chip（轴 B）：横幅之外的**行内**提示。
 *
 * 横幅回答「出了什么事、能怎么办」，这一行只回答「这个标的现在有这件事」——用户在成员列表上
 * 扫一眼就该看见它，而不必先展开分组。`pending` 不渲染（判定尚未成立，画出来会闪）。
 */
function OrphanChip({ member }: { member: AiTradeGroupMemberState }) {
  const wire = member.session?.orphan ?? null;
  if (wire?.state !== "confirmed") return null;
  return (
    <span
      className="truncate text-[10px] text-[var(--short-ink)]"
      data-testid="ai-trade-orphan-chip"
      title="交易所侧存在本会话不知道的持仓；它不受任何自动风控管辖"
    >
      交易所侧有未接管持仓（{wire.qty} 张）
    </span>
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
