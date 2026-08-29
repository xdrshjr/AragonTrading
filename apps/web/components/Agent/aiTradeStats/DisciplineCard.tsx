"use client";

import {
  describeContrarianQuota,
  disciplineRow,
  formatFiredCount,
  summarizeDisciplines,
} from "@/lib/aiTradeDiscipline";
import type {
  AiTradeDirectionRule,
  AiTradeDisciplineRow,
  AiTradeDisciplineWire,
  AiTradeSummaryHealth,
} from "@/lib/aiTradeTypes";

/**
 * 「长期记忆已停摆」的文案（轴 D · D-6）。
 *
 * **必须说明后果**：只报「失败」不报后果，用户无法判断要不要停会话。滚动摘要失败不影响本轮决策
 * ——模型仍在按当前行情决策，丢的是更早的执行历程。
 */
export const SUMMARY_STALLED_TEXT = (failures: number): string =>
  `长期记忆已停摆（连续 ${failures} 次摘要失败）：模型仍在按当前行情决策，` +
  "但会逐渐忘记更早的执行历程（自己挂过什么价、为什么平过仓）。可继续运行，也可停止后重启会话。";

/** 把后端下发的一段提示切成「结论 + 建议」两截；无分隔符即整段一行。 */
function splitHint(hint: string): string[] {
  const parts = hint.split("。").filter((s) => s.trim().length > 0);
  if (parts.length <= 1) return [hint];
  return [`${parts[0]}。`, `${parts.slice(1).join("。")}。`];
}

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
  summaryHealth,
  direction,
}: {
  wire?: AiTradeDisciplineWire | null;
  memberCount?: number;
  /** 轴 D 的第二个渲染落点（`AiTradePanel` 是第一个）：停摆事实必须两处都看得到。 */
  summaryHealth?: AiTradeSummaryHealth | null;
  /**
   * 分组级方向对称性回执（directional-symmetry §7.1）：**服务端解析后的生效值**。
   * 未装配 ⇒ 缺席 ⇒ 逆势通路那一行不渲染。
   */
  direction?: (AiTradeDirectionRule & { minConsensus?: number }) | null;
}) {
  const rows = wire?.rows ?? [];
  // 老后端 / 还没成员 ⇒ 整卡不渲染（绝不在一个没有数据的会话上挂出一排「—」，那会被读成统计坏了）。
  if (!wire || rows.length === 0) return null;
  const summary = summarizeDisciplines(rows);
  const adjustments = wire.adjustments ?? [];
  const naked = disciplineRow(rows, "exchangeBackstop");
  // 逆势通路（directional-symmetry [P2-4]）：配额读**成员级** digest（那是现算的会话状态），
  // 阈值读**分组级**回执（那是服务端解析后的生效值）。判据与文案的唯一真源在 lib。
  const contrarian = describeContrarianQuota(wire.contrarian, direction);

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

      {/* 自检提示（tradability-and-open-funnel 轴 C）：升级为**结构化**渲染——第一句是结论、
          其余是对症建议。合入前它固定建议「改选更宽松的预设」，而本轮实测证明那在短周期断面上是
          错的补救方向（最宽松的档在 BTC 1m 上仍有 93% 否决率）。`data-testid` 保持不变。 */}
      {wire.hint && (
        <div
          className="flex flex-col gap-0.5 rounded border border-[var(--short)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--short-ink)]"
          data-testid="ai-discipline-hint"
        >
          {splitHint(wire.hint).map((line, i) => (
            <p key={line} className={i === 0 ? "font-medium" : "text-[var(--muted)]"}>
              {line}
            </p>
          ))}
        </div>
      )}
      {summaryHealth?.stalled && (
        <p
          className="rounded border border-[var(--accent)] px-2 py-1 text-[10px] leading-relaxed text-[var(--accent-ink)]"
          data-testid="ai-summary-stalled"
        >
          {SUMMARY_STALLED_TEXT(summaryHealth.failures)}
        </p>
      )}

      {naked?.tone === "danger" && (
        <p
          className="rounded border border-[var(--short)] bg-[var(--surface-2)] px-2 py-1 text-[10px] leading-relaxed text-[var(--short-ink)]"
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

      {contrarian && (
        <div
          className="flex flex-col gap-0.5 rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1"
          data-testid="ai-discipline-contrarian"
        >
          <span className="text-[10px] font-medium text-[var(--text)]">
            逆势通路 · 已放开
          </span>
          <span className="text-[10px] leading-relaxed text-[var(--muted)]">{contrarian}</span>
        </div>
      )}

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
