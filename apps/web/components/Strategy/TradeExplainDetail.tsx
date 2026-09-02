"use client";

import clsx from "clsx";
import type { Trade, TriggerExplain, TriggerMetric } from "@/lib/types";
import { explainKindLabel, explainKindTone, resolveExplain } from "@/lib/tradeExplain";
import { fmtPrice } from "@/lib/format";
import FactorContributionBars from "./FactorContributionBars";

/**
 * 成交记录展开详情（auto-trade-explainability §三）。
 *
 * 渐进披露：一句话原因 + 类别角标 → 关键指标对照网格 → 风控快照（交易所侧 OCO）→
 * 条件渲染的因子归因图（仅 factor_score 且有断面）。配色复用既有 token，不新增全局变量。
 *
 * 入参 `explain` 优先（实盘成交表直接传已关联的解释，省去合成 Trade）；否则回落
 * `resolveExplain(trade)`（既有策略面板 / 回测调用方，向后兼容）；两者皆无返回 null
 * （live-fills-explainability §2.5）。
 */
export default function TradeExplainDetail({
  trade,
  explain: explainProp,
}: {
  trade?: Trade;
  explain?: TriggerExplain;
}) {
  const explain = explainProp ?? (trade ? resolveExplain(trade) : null);
  if (!explain) return null;
  return (
    <div className="flex flex-col gap-3 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-3 py-3">
      <Header explain={explain} />
      {explain.summary && (
        <p className="text-[11px] leading-relaxed text-[var(--muted)]">{explain.summary}</p>
      )}
      {explain.metrics.length > 0 && <MetricsGrid metrics={explain.metrics} />}
      {explain.composite && <CompositeBar explain={explain} />}
      {explain.factors && explain.factors.length > 0 && (
        <FactorContributionBars factors={explain.factors} />
      )}
      <RiskSnapshot explain={explain} />
      {explain.meta && <SettlementLine explain={explain} />}
      {explain.meta && <MetaLine explain={explain} />}
    </div>
  );
}

function Header({ explain }: { explain: TriggerExplain }) {
  const tone = explainKindTone(explain.kind);
  return (
    <div className="flex items-center gap-2">
      <span
        className={clsx(
          "rounded-full px-2 py-0.5 text-[10px] font-medium",
          tone === "warn" && "bg-[var(--accent-soft)] text-[var(--accent-ink)]",
          tone === "long" && "bg-[color-mix(in_srgb,var(--long)_16%,transparent)] text-[var(--long-ink)]",
          tone === "short" && "bg-[color-mix(in_srgb,var(--short)_16%,transparent)] text-[var(--short-ink)]",
          tone === "neutral" && "bg-[var(--surface-2)] text-[var(--muted)]",
        )}
      >
        {explainKindLabel(explain.kind)}
      </span>
      <span className="text-xs font-medium text-[var(--text)]">{explain.headline}</span>
    </div>
  );
}

function MetricsGrid({ metrics }: { metrics: TriggerMetric[] }) {
  return (
    // 与 PositionPanel 同款容器自适应：这块网格渲染在 TradesTable 展开行的 <td colSpan> 里，
    // 宽度由「窗口宽 − 49 − 6 − rightPanelWidth」决定，与视口宽同样毫无关系。
    <div
      data-nowrap="scope"
      data-testid="trade-explain-metrics-grid"
      className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-x-4 gap-y-1.5 whitespace-nowrap"
    >
      {metrics.map((m, i) => (
        <div key={`${m.label}-${i}`} className="flex min-w-0 flex-col gap-0.5" title={m.hint}>
          <span className="truncate text-[10px] uppercase tracking-wide text-[var(--muted)]">
            {m.label}
          </span>
          <span
            className="truncate text-xs tabular font-medium"
            style={{ color: metricColor(m.tone) }}
          >
            {m.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 复合得分 vs 进多 / 进空阈值的轴线（factor_score 专属）：得分点位 + 阈值刻度。 */
function CompositeBar({ explain }: { explain: TriggerExplain }) {
  const c = explain.composite;
  if (!c) return null;
  const enterLong = c.enterLong ?? 0.3;
  const enterShort = c.enterShort ?? 0.3;
  // squash 下得分 ∈ [-1,1]，映射到 [0,100]；非 squash 时夹断到该区间仅作可视化。
  const clamp = (v: number) => Math.max(-1, Math.min(1, v));
  const toPct = (v: number) => ((clamp(v) + 1) / 2) * 100;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-[var(--muted)]">
        <span>复合得分 vs 阈值</span>
        <span className="tabular text-[var(--text)]">{c.score.toFixed(4)}</span>
      </div>
      <div className="relative h-3 w-full rounded-sm bg-[var(--surface-2)]">
        <Tick pctValue={toPct(-enterShort)} tone="short" label="进空" />
        <Tick pctValue={toPct(enterLong)} tone="long" label="进多" />
        <div
          className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-[var(--surface)]"
          style={{
            left: `${toPct(c.score)}%`,
            background: c.score >= 0 ? "var(--long)" : "var(--short)",
          }}
          aria-label={`复合得分 ${c.score.toFixed(4)}`}
        />
      </div>
    </div>
  );
}

function Tick({ pctValue, tone, label }: { pctValue: number; tone: "long" | "short"; label: string }) {
  return (
    <div
      className="absolute top-0 h-full w-px"
      style={{ left: `${pctValue}%`, background: `var(--${tone})`, opacity: 0.6 }}
      title={label}
      aria-hidden
    />
  );
}

function RiskSnapshot({ explain }: { explain: TriggerExplain }) {
  const risk = explain.risk;
  if (!risk) return null;
  const sl = risk.stopLossPrice;
  const tp = risk.takeProfitPrice;
  if (sl == null && tp == null && risk.maxHoldBars == null) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--border)] pt-2 text-[11px]">
      <span className="text-[10px] uppercase tracking-wide text-[var(--muted)]">风控快照</span>
      {sl != null && (
        <span className="tabular" style={{ color: "var(--short-ink)" }}>
          止损 @ {fmtPrice(sl)}
        </span>
      )}
      {tp != null && (
        <span className="tabular" style={{ color: "var(--long-ink)" }}>
          止盈 @ {fmtPrice(tp)}
        </span>
      )}
      {risk.maxHoldBars != null && (
        <span className="tabular text-[var(--muted)]">最长持有 {risk.maxHoldBars} 根</span>
      )}
    </div>
  );
}

/**
 * 结算价来源（fill-truth-and-effective-controls 轴 A / §十三 Q6 的裁定）。
 *
 * 这是 `settlementSource` / `settlementPosId` / `settlementPriceGapBps` 三个键的**渲染落点**。
 * 没有它，这三个键就是原则 6 明令禁止的「有写点没读点」——而它们回答的恰恰是本轮最想问的
 * 那个问题：这条外部平仓的价格，到底是交易所说的，还是我们自己按标记价猜的？差多少？
 *
 * 整键缺席（轴 A 未装配 / 非外部平仓 / 老数据）⇒ 整行不渲染。
 */
function SettlementLine({ explain }: { explain: TriggerExplain }) {
  const meta = explain.meta;
  if (!meta?.settlementSource) return null;
  const exchange = meta.settlementSource === "exchange";
  const gap = meta.settlementPriceGapBps;
  return (
    <p
      className={exchange ? "text-[10px] text-[var(--muted)]" : "text-[10px] text-[var(--warn)]"}
      data-testid="trade-settlement-source"
    >
      <span className="font-medium">
        {exchange ? "结算价来自交易所" : "结算价为本地标记价（未取回交易所成交价）"}
      </span>
      {exchange && meta.settlementPosId ? ` · 持仓号 ${meta.settlementPosId}` : ""}
      {gap != null ? ` · 与侦测时标记价差 ${gap >= 0 ? "+" : ""}${+gap.toFixed(1)} bps` : ""}
      {/* 费率残差的**可验算落点**（评审 RV-P2-5 点名要给 `bookedOpenFeeUsd` 一个读点）：这条腿的
          `fee` = 交易所往返总费 − 本地已记的开仓腿费。两个被减数都摆出来，用户（与事后对账）
          才能自己把那个减法做一遍——只给结果就等于要求他们相信一个算不出来的数。 */}
      {exchange && meta.exchangeFeeUsd != null
        ? ` · 往返总费 ${+meta.exchangeFeeUsd.toFixed(4)}`
          + (meta.bookedOpenFeeUsd != null
            ? `（已记开仓腿 ${+meta.bookedOpenFeeUsd.toFixed(4)}）` : "")
        : ""}
      {meta.settlementWaitedS != null ? ` · 等待 ${meta.settlementWaitedS}s` : ""}
      {meta.settlementRejectReason
        ? ` · 降级原因：${describeSettlementReject(meta.settlementRejectReason)}`
        : ""}
    </p>
  );
}

/**
 * 结算降级原因码 → 中文。
 *
 * 后端的机器码绝不裸着示人（本仓库为这条纪律付过多次代价，`DEGRADE_REASON_LABELS` 是同一范式）。
 * 未登记的码**原样透出**而不是隐藏：显示一个我们没预料到的原因，好过让用户以为没有原因。
 */
const SETTLEMENT_REJECT_LABELS: Record<string, string> = {
  fee_residual_negative: "交易所往返总费小于本地已记的开仓腿费，账不自洽，已退回本地标记价",
};

function describeSettlementReject(code: string): string {
  return SETTLEMENT_REJECT_LABELS[code] ?? code;
}

function MetaLine({ explain }: { explain: TriggerExplain }) {
  const meta = explain.meta;
  if (!meta) return null;
  const parts: string[] = [];
  if (meta.strategyName) parts.push(meta.strategyName);
  if (meta.instId) parts.push(meta.instId);
  if (meta.bar) parts.push(meta.bar);
  if (meta.clOrdId) parts.push(`单号 ${meta.clOrdId}`);
  if (parts.length === 0) return null;
  return <p className="text-[10px] text-[var(--muted)]">{parts.join(" · ")}</p>;
}

function metricColor(tone?: TriggerMetric["tone"]): string {
  if (tone === "long") return "var(--long)";
  if (tone === "short") return "var(--short)";
  if (tone === "warn") return "var(--accent)";
  return "var(--text)";
}
