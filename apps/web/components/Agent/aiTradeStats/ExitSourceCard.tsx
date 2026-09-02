"use client";

import {
  exitSourceColor,
  exitSourceLabel,
  formatExitHold,
  formatExitWinRate,
  hasExitAttribution,
  orderedExitRows,
} from "@/lib/aiTradeAcceptance";
import type { AiTradeExitMix } from "@/lib/aiTradeTypes";
import { colorForReturn, signed } from "@/lib/format";

/**
 * 退出来源卡（ai-trade-horizon-acceptance-loop 轴 B）。
 *
 * 上一轮改了三处 prompt 文案，把「主动 close 永远优先于等待保护层」改成「让保护线执行退出同样
 * 正确」。这条改动的**唯一**验收方式就是「保护层平仓占比是否上升」——本卡就是那个读数。调查里
 * 那组「16 次往返、15 次 Agent 主动平仓、唯一一次保护层平仓反而净赚 1.87 USDT」的对比，此前
 * 只能靠人肉翻流水得到。
 *
 * **props 与验收卡是两个独立入参**，绝不从 `acceptance?.exitMix` 里掏（[P0-4]）：`acceptance`
 * 在未装配周期模式时为 null，而「关掉周期模式跑一段对照组」恰恰是上线观察窗口最需要做的事——
 * 挂在它下面会让对照臂记了数据却一个字都看不到。
 *
 * `exitMix` **不参与任何判定**（无 verdict）：保护层平仓占比高本身既不是好事也不是坏事，把它
 * 做成门槛会诱导出「故意不主动平仓、等保护层」这种劣化行为。它是记录项 + 诊断输入。
 *
 * ai-trade-loss-and-liveness-hardening 轴 F：**上面那条理由本轮不推翻**，但「不做硬门槛」不等于
 * 「什么都不做」。卡底新增一行由后端下发的、**数据化的**结论（`exitDiscretion`）——它仍然不是
 * 一道闸，只是把「我们早就看见了却没动」的那个观察说出来，并给一个能真的去改的入口。
 */
export default function ExitSourceCard({
  exits,
  onOpenProfitGuard,
}: {
  exits: AiTradeExitMix | null | undefined;
  /** 「去开启利润保护」的落点；未提供 ⇒ 只渲染结论、不渲染按钮（绝不给一个点了没反应的按钮）。 */
  onOpenProfitGuard?: () => void;
}) {
  if (!hasExitAttribution(exits)) return null;
  const mix = exits as AiTradeExitMix;
  const rows = orderedExitRows(mix);
  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-exit-source-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">退出来源</span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          {mix.totalExits} 笔 · 样本 ≥ 5 才给胜率
        </span>
      </div>

      {/* 堆叠比例条：一眼看出「谁在平仓」。色值取自既有 --chart-* token，不新增设计 token。 */}
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
        {rows.map((row) => (
          <span
            key={row.source}
            className="h-full"
            style={{
              width: `${((row.count / Math.max(1, mix.totalExits)) * 100).toFixed(2)}%`,
              backgroundColor: exitSourceColor(row.source),
            }}
            title={`${exitSourceLabel(row.source)} ${row.count} 笔`}
          />
        ))}
      </div>

      <div className="flex flex-col gap-0.5">
        {rows.map((row) => (
          <div
            key={row.source}
            className="flex items-center justify-between gap-2 text-[11px]"
            data-testid={`ai-exit-row-${row.source}`}
          >
            <span className="flex min-w-0 flex-1 items-center gap-1 truncate text-[var(--text)]">
              <span
                className="inline-block h-2 w-2 shrink-0 rounded-[2px]"
                style={{ backgroundColor: exitSourceColor(row.source) }}
              />
              {exitSourceLabel(row.source)}
            </span>
            <span className="tabular shrink-0 text-[var(--muted)]">
              {row.count} 笔 · 胜率 {formatExitWinRate(row)} · 均持 {formatExitHold(row)}
            </span>
            <span
              className="tabular w-[72px] shrink-0 text-right font-medium"
              style={{ color: colorForReturn(row.netUsd) }}
            >
              {signed(row.netUsd)}
            </span>
          </div>
        ))}
      </div>

      {mix.exitDiscretion && (
        // 轴 F：**文案整句由后端下发**，前端一个字都不拼——它是一句带数字的判定性说明，
        // 两处各写一遍必然与后端真正据以判定的数字分叉（`horizon-profiles` 的成文纪律）。
        <div
          className={
            mix.exitDiscretion.level === "adverse"
              ? "flex flex-col gap-1 rounded border border-[var(--short)] bg-[var(--short-soft)] px-2 py-1.5"
              : "flex flex-col gap-1 rounded border border-[var(--border)] bg-[var(--surface-2)] px-2 py-1.5"
          }
          data-testid="ai-exit-discretion"
          data-level={mix.exitDiscretion.level}
        >
          <p className="text-[10px] leading-relaxed text-[var(--text)]">
            {mix.exitDiscretion.note}
          </p>
          {mix.exitDiscretion.level === "adverse" && onOpenProfitGuard && (
            <button
              type="button"
              onClick={onOpenProfitGuard}
              className="self-start rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 text-[10px] text-[var(--text)] hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
              data-testid="ai-exit-discretion-cta"
            >
              去开启利润保护
            </button>
          )}
        </div>
      )}

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        按<span className="text-[var(--text)]">平仓</span>成交的退出路径归集，
        <span className="text-[var(--text)]">不参与验收判定</span>
        ——保护层占比高本身既不是好事也不是坏事，把它做成门槛会诱导「故意不平仓等保护层」。
        归因总闸关闭的会话与本功能上线前的历史成交落在「未记录来源」。
      </p>
    </div>
  );
}
