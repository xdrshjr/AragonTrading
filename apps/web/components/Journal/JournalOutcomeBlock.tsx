"use client";

import { Badge } from "@/components/ui";
import {
  degradeLabel,
  exitSourceLabel,
  formatNumber,
  linkConfidenceGlyph,
  linkConfidenceLabel,
  linkConfidenceTone,
} from "@/lib/journal";
import type { JournalFillRef, JournalOutcome } from "@/lib/journalTypes";

/**
 * 执行与结果（ai-trade-audit-journal §2.6.2 第 6 段）。
 *
 * 成交（价 / 量 / 费 / 净额盈亏 / 持仓后）+ 订单回执（clOrdId / ordId / 状态 / 拒单码）
 * + 往返结果（已实现 bps / R 倍数 / 退出归因 / 持仓时长）+ **关联置信度徽标**。
 *
 * 关联置信度是「形状 + 文案 + 颜色」三重编码，绝不靠颜色单独承载语义。
 * 未成交轮显示「本轮未下单」+ 原因中文化——空白会被读成「界面坏了」。
 *
 * `_post_round` 产生的那条腿必须明说它的出处：利润保护在决策轮末平掉的那一笔**不是模型的
 * 动作**，不写清楚的话它在日志台上会像是凭空出现的。
 */
function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="whitespace-nowrap text-[11px] text-[var(--muted)]">{label}</span>
      <span className="tabular text-xs">{value}</span>
    </div>
  );
}

export default function JournalOutcomeBlock({
  outcome,
  fillRef,
}: {
  outcome: JournalOutcome;
  fillRef: JournalFillRef | null;
}) {
  const legs = fillRef?.legs ?? [];
  const trip = outcome.trip;
  const order = outcome.order;

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={linkConfidenceTone(outcome.linkConfidence)}>
          <span aria-hidden="true" className="mr-1">
            {linkConfidenceGlyph(outcome.linkConfidence)}
          </span>
          {linkConfidenceLabel(outcome.linkConfidence)}
        </Badge>
        {outcome.fillSource === "post_round" && (
          <span className="text-[11px] text-[var(--muted)]">
            本笔由利润保护在决策轮末触发，不是模型的动作
          </span>
        )}
      </div>

      {!legs.length && (
        <p className="text-xs text-[var(--muted)]">
          本轮未下单
          {outcome.noFillReason ? `：${degradeLabel(outcome.noFillReason) || outcome.noFillReason}` : "。"}
        </p>
      )}

      {legs.length > 0 && (
        <div className="min-w-0 overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs">
            <thead className="text-[var(--muted)]">
              <tr>
                {["腿", "方向", "价格", "数量", "手续费", "盈亏", "来源", "单号"].map((label) => (
                  <th
                    key={label}
                    scope="col"
                    className="whitespace-nowrap px-2 py-1.5 text-left font-medium"
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {legs.map((leg, index) => (
                <tr key={`${leg.clOrdId ?? "leg"}-${index}`} className="border-b border-[var(--border)]">
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">{index + 1}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">{leg.side ?? "—"}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">{formatNumber(leg.px, 4)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">{formatNumber(leg.sz, 4)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">{formatNumber(leg.fee, 4)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular">{formatNumber(leg.pnl, 4)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-[var(--muted)]">
                    {leg.source === "post_round" ? "轮末保护平仓" : "本轮决策"}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-[var(--muted)]">
                    {leg.clOrdId ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {order && (
        <section className="rounded-[10px] border border-[var(--border)] p-2">
          <h4 className="mb-1 text-[11px] font-medium text-[var(--muted)]">订单回执</h4>
          <Row label="状态" value={order.status} />
          <Row label="clOrdId" value={order.clOrdId || "—"} />
          <Row label="ordId" value={order.ordId || "—"} />
          <Row label="拒单码" value={order.errorCode || "—"} />
        </section>
      )}

      {trip ? (
        <section className="rounded-[10px] border border-[var(--border)] p-2">
          <h4 className="mb-1 text-[11px] font-medium text-[var(--muted)]">往返结果</h4>
          <Row label="已实现盈亏 (USDT)" value={formatNumber(trip.pnl, 4)} />
          <Row
            label="已实现 bps"
            value={trip.realizedBps === undefined ? "—" : formatNumber(trip.realizedBps, 2)}
          />
          <Row
            label="R 倍数"
            value={trip.rMultiple === undefined ? "—" : formatNumber(trip.rMultiple, 2)}
          />
          <Row label="退出归因" value={exitSourceLabel(trip.exitSource) || "—"} />
          <Row
            label="持仓时长 (秒)"
            value={trip.holdSeconds === undefined ? "—" : formatNumber(trip.holdSeconds, 0)}
          />
        </section>
      ) : (
        legs.length > 0 && (
          <p className="text-[11px] text-[var(--muted)]">
            该笔尚未产生完整往返结果（仓位仍在持有中，或平仓腿还没落库）。
            缺的读数一律留空，绝不补 0。
          </p>
        )
      )}
    </div>
  );
}
