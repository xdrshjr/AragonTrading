"use client";

import {
  budgetProgress,
  describeBackstop,
  describeBudget,
  describeCooldown,
  describeVetoes,
  turnoverSeverity,
} from "@/lib/aiTradeTurnover";
import type { TurnoverRow, TurnoverTone } from "@/lib/aiTradeTurnover";
import type { AiTradeBackstopWire, AiTradeTurnoverWire } from "@/lib/aiTradeTypes";

export interface TurnoverBackstopMember {
  symbol: string;
  turnover?: AiTradeTurnoverWire | null;
  backstop?: AiTradeBackstopWire | null;
}

/**
 * 换手纪律与兜底卡（ai-trade-turnover-budget-and-backstop §2.4.2）。
 *
 * 本卡回答两个用户此前完全看不见的问题：**「我为什么开不出单」**与**「交易所那边到底还挂着
 * 止损吗」**。第二个问题此前只有一句从未核实过的承诺，而它恰是跨日模式「不必每轮盯盘」的唯一
 * 前提。
 *
 * 三条渲染纪律：
 * 1. **前端零计算**：所有数字由后端下发，本组件只排版（`lib/aiTradeTurnover` 只做中文化）。
 * 2. **按成员渲染，绝不跨成员求和**（[P1-2]）：每个成员是独立会话、独立持仓、**独立令牌桶与
 *    独立兜底覆盖率**，求和后的数不对应任何一条判定。
 * 3. **「未核实」与「覆盖未知」都用灰色**（沿用验收卡 D-6）：黄色会被读成「快好了」，而这两态
 *    的语义是「还不知道」。只有 `mismatch` 与「有数且覆盖不足」用红色——它们是确定的坏消息。
 *
 * 全部成员都没有这两个键 ⇒ **整卡不渲染**（沿用同页 `HorizonLiveCard` 的既有纪律：绝不在没有
 * 契约的会话上挂一排 0）。
 */
export default function TurnoverBackstopCard({
  members,
}: {
  members: TurnoverBackstopMember[];
}) {
  // 先把每个成员真正**有内容的行**算出来，再决定整卡渲不渲染：一个成员可能带着 `turnover` 键
  // 却两道闸都结构性关闭（custom 全默认）——此时若只按「键在不在」判，卡片会渲染成一个空壳，
  // 那正是本页「绝不在没有契约的会话上挂一排 0」纪律要防的形态。
  const blocks = members
    .map((member) => ({ member, rows: buildRows(member) }))
    .filter((block) => block.rows.length > 0)
    // 「最紧要优先」：mismatch > 覆盖不足 > 冷却中 > 预算耗尽 > 正常。
    .sort(
      (a, b) =>
        turnoverSeverity(a.member.turnover, a.member.backstop) -
        turnoverSeverity(b.member.turnover, b.member.backstop),
    );
  if (blocks.length === 0) return null;
  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-turnover-backstop-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">换手纪律与兜底</span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          预算按<span className="text-[var(--text)]">本会话</span>计 · 重启后重新计算
        </span>
      </div>

      {blocks.map((block) => (
        <MemberBlock
          key={block.member.symbol}
          member={block.member}
          rows={block.rows}
          multi={blocks.length > 1}
        />
      ))}

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        这两道闸<span className="text-[var(--text)]">只约束建立新敞口的那一腿</span>：平仓、观望、
        撤单与同向加仓永不受限；提议反手时
        <span className="text-[var(--text)]">平仓腿一定会执行</span>，只有反向的新开仓腿会被推迟。
        「覆盖未知」是<span className="text-[var(--text)]">还不知道</span>、不是「没有兜底」——
        交易所未回报可核对的委托张数时如实显示为未知，此时系统不会因此拦下任何操作。
      </p>
    </div>
  );
}

/** 一个成员真正有内容的行（全空 ⇒ 该成员整块不渲染，见上）。 */
function buildRows(member: TurnoverBackstopMember): TurnoverRow[] {
  return [
    describeCooldown(member.turnover),
    describeBudget(member.turnover),
    describeVetoes(member.turnover),
    describeBackstop(member.backstop),
  ].filter((row): row is TurnoverRow => row !== null);
}

function MemberBlock({
  member,
  rows,
  multi,
}: {
  member: TurnoverBackstopMember;
  rows: TurnoverRow[];
  multi: boolean;
}) {
  const progress = budgetProgress(member.turnover);
  return (
    <div className="flex flex-col gap-0.5" data-testid={`ai-turnover-${member.symbol}`}>
      {multi ? (
        <span className="truncate text-[11px] font-medium text-[var(--text)]">
          {member.symbol}
        </span>
      ) : null}
      {rows.map((row) => (
        <div
          key={row.key}
          className="flex items-center justify-between gap-2 text-[11px]"
          data-testid={`ai-turnover-row-${row.key}`}
        >
          <span className="shrink-0 text-[var(--muted)]">{row.label}</span>
          <span className="min-w-0 flex-1 truncate text-right text-[10px] text-[var(--muted)]">
            {row.detail}
          </span>
          <span
            className="tabular w-[104px] shrink-0 text-right font-medium"
            style={{ color: toneColor(row.tone) }}
          >
            {row.value}
          </span>
        </div>
      ))}
      {progress == null ? null : (
        <div
          className="h-1 w-full overflow-hidden rounded-full bg-[var(--surface-2)]"
          data-testid="ai-turnover-budget-bar"
        >
          <span
            className="block h-full bg-[var(--accent)]"
            style={{ width: `${(progress * 100).toFixed(1)}%` }}
          />
        </div>
      )}
    </div>
  );
}

/** 色调 → CSS 变量。灰（muted）是「还不知道」，红（short）才是确定的坏消息——绝不用黄色。 */
function toneColor(tone: TurnoverTone): string {
  if (tone === "short") return "var(--short)";
  if (tone === "long") return "var(--long)";
  if (tone === "accent") return "var(--accent)";
  return "var(--muted)";
}
