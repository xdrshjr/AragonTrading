"use client";

import {
  ACCEPTANCE_VERDICT_META,
  hasAcceptance,
  verdictColor,
} from "@/lib/aiTradeAcceptance";
import type { AiTradeAcceptance, AiTradeAcceptanceRow } from "@/lib/aiTradeTypes";
import { colorForReturn, signed } from "@/lib/format";

/**
 * 周期验收卡（ai-trade-horizon-acceptance-loop 轴 C）。
 *
 * 调查原文最后一段点名的五个验收指标汇成一张卡，回答的是**上一轮的机制到底有没有把钱赚回来**：
 * 平均持仓时间 / 落在目标区间的比例 / 每日换手次数（墙钟）/ 手续费占毛利润 / 资金费后净收益。
 * 三处（运行期看板 / 历史详情 / 回测面板）共用本组件与同一份 props 类型，形状本来就同源。
 *
 * 六条设计约束（对应「美观、优雅、符合人机交互最佳实践」）：
 * 1. **一屏一结论**：卡头右上角是整卡结论徽标，三态三色（`insufficient` 用**灰**不用黄——
 *    黄会被读成「快好了」，而样本不足什么都不是）。
 * 2. 每行固定栅格：标签 / 实测值 / 门槛 / 徽标。数值列一律 `tabular` 等宽数字，否则刷新时
 *    数字宽度跳动。
 * 3. **门槛与实测并排，不用进度条**：五行的量纲互不相同（秒 / 比例 / 次数 / 比例 / USD），
 *    同一根进度条会暗示可比性。
 * 4. `insufficient` 时整卡降饱和并把样本进度写在卡底，**绝不**把任一行渲染成绿色。
 * 5. note 以 title 挂在行上（第 3 行的墙钟 vs 在场口径尤其必须说清，否则运维会把一个宽门槛
 *    当成强保证）。
 * 6. 未装配周期模式 / 无验收数据 ⇒ **整卡不渲染**。
 */
export default function HorizonAcceptanceCard({
  acceptance,
}: {
  acceptance: AiTradeAcceptance | null | undefined;
}) {
  if (!hasAcceptance(acceptance)) return null;
  const card = acceptance as AiTradeAcceptance;
  const meta = ACCEPTANCE_VERDICT_META[card.verdict] ?? ACCEPTANCE_VERDICT_META.insufficient;
  const dimmed = card.verdict === "insufficient";
  return (
    <div
      className={`flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2${
        dimmed ? " opacity-70" : ""
      }`}
      data-testid="ai-horizon-acceptance"
      data-verdict={card.verdict}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">
          周期验收 · <span className="text-[var(--text)]">{card.title || card.profileId}</span>
        </span>
        <span
          className="shrink-0 text-[11px] font-semibold"
          style={{ color: meta.color }}
          data-testid="ai-acceptance-verdict"
        >
          {meta.icon} {meta.label}
        </span>
      </div>

      <div className="flex flex-col gap-0.5">
        {card.rows.map((row) => (
          <AcceptanceRow key={row.key} row={row} />
        ))}
      </div>

      {card.bySymbol && card.bySymbol.length > 1 && (
        <div
          className="flex flex-col gap-0.5 border-t border-[var(--border)] pt-1"
          data-testid="ai-acceptance-by-symbol"
        >
          <span className="text-[10px] text-[var(--muted)]">
            逐标的（合并只用于上方总判定，一个赚一个亏会被平均掉）
          </span>
          {card.bySymbol.map((row) => (
            <div key={row.instId} className="flex items-center justify-between gap-2 text-[10px]">
              <span className="min-w-0 flex-1 truncate text-[var(--text)]">{row.instId}</span>
              <span className="tabular shrink-0 text-[var(--muted)]">{row.roundTrips} 笔</span>
              <span
                className="tabular w-[72px] shrink-0 text-right"
                style={{ color: colorForReturn(row.netPnlUsd) }}
              >
                {signed(row.netPnlUsd)}
              </span>
              <span
                className="w-[14px] shrink-0 text-right"
                style={{ color: verdictColor(row.verdict ?? "insufficient") }}
              >
                {(ACCEPTANCE_VERDICT_META[row.verdict ?? "insufficient"] ??
                  ACCEPTANCE_VERDICT_META.insufficient).icon}
              </span>
            </div>
          ))}
        </div>
      )}

      {card.diagnostics.length > 0 && (
        <ul className="flex flex-col gap-0.5 border-t border-[var(--border)] pt-1">
          {card.diagnostics.map((line) => (
            <li key={line} className="text-[10px] leading-relaxed text-[var(--muted)]">
              ⚠ {line}
            </li>
          ))}
        </ul>
      )}

      <p className="text-[10px] leading-relaxed text-[var(--muted)]" data-testid="ai-acceptance-sample">
        {card.sampleNote}
      </p>
    </div>
  );
}

// 一行判据：标签 / 实测值 / 门槛 / 徽标。note 走 title（行内展开会把五行卡撑成一屏）。
function AcceptanceRow({ row }: { row: AiTradeAcceptanceRow }) {
  const meta = ACCEPTANCE_VERDICT_META[row.verdict] ?? ACCEPTANCE_VERDICT_META.insufficient;
  return (
    <div
      className="flex items-center justify-between gap-2 text-[11px]"
      title={row.note ?? undefined}
      data-testid={`ai-acceptance-row-${row.key}`}
      data-verdict={row.verdict}
    >
      <span className="min-w-0 flex-1 truncate text-[var(--text)]">
        {row.label}
        {row.note ? <span className="ml-0.5 text-[var(--muted)]">ⓘ</span> : null}
      </span>
      <span
        className="tabular w-[92px] shrink-0 text-right font-medium"
        style={{ color: verdictColor(row.verdict) }}
      >
        {row.display}
      </span>
      <span className="tabular w-[80px] shrink-0 text-right text-[10px] text-[var(--muted)]">
        {row.target}
      </span>
      <span className="w-[14px] shrink-0 text-right" style={{ color: meta.color }}>
        {meta.icon}
      </span>
    </div>
  );
}
