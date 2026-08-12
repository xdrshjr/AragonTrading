"use client";

import {
  disciplineRow,
  formatFiredCount,
  summarizeDisciplines,
} from "@/lib/aiTradeDiscipline";
import type { AiTradeDisciplineRow, AiTradeDisciplineWire } from "@/lib/aiTradeTypes";

/**
 * 生效纪律自检卡（ai-trade-discipline-preset-and-integrity 轴 B 的明细落点）。
 *
 * 存在的理由：本轮把「已经造好但一直是暗的」那几层纪律默认装配起来了，用户会立刻观察到「AI 开单
 * 变少」。如果面板只说「已启用」而说不出**是哪一条在拦**，这个变化就只会被读成「AI 坏了」，而
 * 「一键关回 off」正是本轮价值主张最容易死掉的地方。
 *
 * 三条呈现纪律（都对应踩过的坑）：
 * - `armed=false` 的行必须显示后端给的 note（为什么没生效、怎么开），绝不留一片无解释的灰；
 * - `firedCount == null` 显示「—」而不是 0——「算不出」与「一次都没触发」是两个结论；
 * - `hint` 是「一堆闸武装着却一单没开过」的恒真自检，出现时必须**显眼**，它是本轮最重要的一句话。
 */
export default function DisciplineCard({
  wire,
  memberCount = 1,
}: {
  wire?: AiTradeDisciplineWire | null;
  memberCount?: number;
}) {
  const rows = wire?.rows ?? [];
  // 老后端 / 还没成员 ⇒ 整卡不渲染（绝不在一个没有数据的会话上挂出一排「—」，那会被读成统计坏了）。
  if (!wire || rows.length === 0) return null;
  const summary = summarizeDisciplines(rows);
  const adjustments = wire.adjustments ?? [];
  const naked = disciplineRow(rows, "exchangeBackstop");

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-discipline-card"
    >
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-[var(--muted)]">
          交易纪律 · {wire.presetLabel ?? wire.preset}
        </span>
        <span className="tabular text-[10px] text-[var(--muted)]">{summary.label}</span>
      </div>

      {wire.hint && (
        <p
          className="rounded border border-[var(--short)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--short)]"
          data-testid="ai-discipline-hint"
        >
          {wire.hint}
        </p>
      )}

      {naked?.tone === "danger" && (
        <p
          className="rounded border border-[var(--short)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--short)]"
          data-testid="ai-discipline-naked"
        >
          {naked.note}
        </p>
      )}

      <div className="flex flex-col divide-y divide-[var(--border)]">
        {rows.map((row) => (
          <DisciplineRowView key={row.key} row={row} />
        ))}
      </div>

      {memberCount > 1 && (
        <p className="text-[10px] leading-relaxed text-[var(--muted)]">
          多标的分组：预设与阈值是<strong className="text-[var(--text)]">分组共享</strong>的，
          但「交易所侧兜底」是<strong className="text-[var(--text)]">逐标的</strong>的读数，
          本表只显示其中一个标的。逐标的的覆盖率与「立即补挂」见下方「换手纪律与兜底」卡片。
        </p>
      )}

      {adjustments.length > 0 && (
        <div className="flex flex-col gap-0.5" data-testid="ai-discipline-adjustments">
          <span className="text-[10px] font-medium text-[var(--muted)]">服务端调整</span>
          {adjustments.map((adj) => (
            <p key={`${adj.field}-${adj.reason}`} className="text-[10px] leading-relaxed text-[var(--muted)]">
              · {adj.reason}
              {adj.from != null && adj.to != null ? `（${adj.from} → ${adj.to}）` : ""}
            </p>
          ))}
        </div>
      )}

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        阈值与判据全部由服务端下发，前端不复制任何一个数。「触发」列是
        <strong className="text-[var(--text)]">本会话累计</strong>被这一条拦下的次数；显示「—」
        表示这一条算不出计数，不代表 0。本组阈值尚未经回测校准，可在启动弹窗改选预设。
      </p>
    </div>
  );
}

function DisciplineRowView({ row }: { row: AiTradeDisciplineRow }) {
  const color =
    row.tone === "danger"
      ? "var(--short)"
      : row.tone === "warn"
        ? "var(--accent)"
        : row.armed
          ? "var(--long)"
          : "var(--muted)";
  return (
    <div className="flex flex-col gap-0.5 py-1" data-testid={`ai-discipline-row-${row.key}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span aria-hidden className="text-[10px]" style={{ color }}>
            ●
          </span>
          <span className="truncate text-[11px] text-[var(--text)]">{row.label}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="tabular text-[10px]" style={{ color }}>
            {row.armed ? (row.threshold ?? "已启用") : "未生效"}
          </span>
          <span className="tabular text-[10px] text-[var(--muted)]">
            {formatFiredCount(row.firedCount)}
          </span>
        </span>
      </div>
      {row.note && (
        <span className="truncate text-[10px] leading-relaxed text-[var(--muted)]" title={row.note}>
          {row.note}
        </span>
      )}
    </div>
  );
}
