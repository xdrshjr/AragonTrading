"use client";

import {
  coverageLabel, describeIntegrity, economicsSourceLabel,
  executionReasonLabel, integrityDetailText, readIntegrity,
  type EconomicsSnapshot, type IntegritySnapshot,
} from "@/lib/aiTradeIntegrity";
import { colorForReturn, fmtTime } from "@/lib/format";
import { sumBlocked } from "@/lib/aiTradeFunnel";

function valueText(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value)
    ? "暂不可得" : value.toLocaleString("zh-CN", {
      minimumFractionDigits: 2, maximumFractionDigits: 6,
    });
}

function Reading({ label, value, money = false }: {
  label: string; value: number | null | undefined; money?: boolean;
}) {
  const known = value != null && Number.isFinite(value);
  return (
    <div className="flex min-w-0 justify-between gap-2 text-[11px]">
      <dt className="min-w-0 break-words text-[var(--muted)]">{label}</dt>
      <dd className="tabular shrink-0" style={{
        color: known && money ? colorForReturn(value) : "var(--text)",
      }}>
        {known && !money ? value : valueText(value)}
        {known && money ? " USDT" : ""}
      </dd>
    </div>
  );
}

/** 只展示服务端经济读数；不从环形缓冲成交重算全程费用。 */
export function EconomicsReadings({ economics }: {
  economics?: EconomicsSnapshot | null;
}) {
  const e = economics;
  const official = e?.basis === "exchange_settled" || e?.basis === "mixed"
    || e?.exchangeRealizedPnlUsd != null;
  return (
    <div className="min-w-0 space-y-1.5" data-testid="ai-integrity-economics">
      <p className="text-[10px] text-[var(--muted)]">
        {e?.scope === "group" ? "全组" : "本运行"} ·
        {economicsSourceLabel(e?.basis)} ·
        {coverageLabel(e?.coverage)}
      </p>
      <dl className="space-y-1">
        <Reading label="交易净额（含双腿手续费）" value={e?.tradingNetUsd} money />
        <Reading label="资金费" value={e?.fundingCostUsd} money />
        <Reading label="含资金费净额" value={e?.netAfterFundingUsd} money />
      </dl>
      <p className="text-[10px] text-[var(--muted)]">
        资金费来源：{e?.fundingBasis === "exchange_settled" ? "官方结算"
          : e?.fundingBasis === "accrued" ? "本地计提" : "暂不可得"}
      </p>
      {official && <dl className="space-y-1 border-t border-[var(--border)] pt-2">
        <Reading label="官方交易净额" value={e?.exchangeTradingNetUsd} money />
        <Reading label="官方资金费" value={e?.exchangeFundingCostUsd} money />
        <Reading label="官方含资金费净额" value={e?.exchangeNetAfterFundingUsd} money />
        <Reading label="清算费用" value={e?.liquidationCostUsd} money />
        <Reading label="结算调整" value={e?.settlementAdjustmentUsd} money />
        <Reading label="官方最终已实现结果" value={e?.exchangeRealizedPnlUsd} money />
      </dl>}
    </div>
  );
}

export default function IntegrityCard({ instId, integrity }: {
  instId: string; integrity?: IntegritySnapshot | null;
}) {
  const wire = readIntegrity(integrity);
  const diagnosis = describeIntegrity(integrity);
  const quota = wire?.entryQuota;
  return (
    <section className="min-w-0 space-y-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-integrity-card" aria-label={`${instId} 运行诊断`}>
      <h3 className="break-words text-xs font-medium text-[var(--text)]">
        {instId} · 运行诊断
      </h3>
      <p className="break-words text-[11px] text-[var(--text)]" title={diagnosis}>
        {diagnosis}
      </p>
      <p className="text-[10px] text-[var(--muted)]">
        起始 {wire?.runStartedTs != null ? fmtTime(wire.runStartedTs) : "暂不可得"}
        {" · 更新 "}{wire?.asOfTs != null ? fmtTime(wire.asOfTs) : "暂不可得"}
      </p>
      <EconomicsReadings economics={wire?.economics} />
      {quota?.coverage === "legacy_unknown" &&
        <p className="text-[10px] text-[var(--muted)]">
          旧额度不可确认，剩余按0处理
        </p>}
      <IntegrityDetails wire={wire} />
    </section>
  );
}

function IntegrityDetails({ wire }: { wire: IntegritySnapshot | null }) {
  const funnel = wire?.funnel;
  const quota = wire?.entryQuota;
  const detail = integrityDetailText(wire?.diagnosis?.details);
  return (
      <details className="min-w-0 text-[11px]">
        <summary className="cursor-pointer text-[var(--accent-ink)]">
          展开运行明细
        </summary>
        <p className="my-1 text-[10px] text-[var(--muted)]">
          {funnel?.scope ?? "当前运行段范围暂不可得"} ·
          {coverageLabel(funnel?.coverage)}
        </p>
        <dl className="space-y-1">
          <Reading label="完成轮数" value={wire?.sampleThroughRound} />
          <Reading label="模型自主观望" value={funnel?.modelHold} />
          <Reading label="开仓提议" value={funnel?.intents} />
          <Reading label="首拦截层" value={funnel ? sumBlocked(funnel) : null} />
          <Reading label="仓位解零" value={funnel?.sizingZeroed} />
          <Reading label="执行未提交" value={funnel?.executionBlocked
            ? Object.values(funnel.executionBlocked).reduce((a, b) => a + b, 0)
            : null} />
          <Reading label="已接收订单" value={funnel?.submitted} />
          <Reading label="回执待核实" value={funnel?.outcomeUnknown} />
          <Reading label="成交腿" value={funnel?.filled} />
          <Reading label="逆势剩余额度" value={quota?.remaining} />
          <Reading label="额度已占用" value={quota?.used} />
          {(quota?.legacyAssumedUsed ?? 0) > 0 &&
            <Reading label="其中旧数据假定占用" value={quota?.legacyAssumedUsed} />}
          <Reading label="额度预留" value={quota?.reserved} />
          <Reading label="完整费用样本" value={wire?.economics?.completeFeeTripCount} />
          <Reading label="未知样本" value={wire?.economics?.unknownTripCount} />
          <Reading label="全程手续费（含未平开仓）" value={wire?.economics?.sessionFeeUsd} money />
        </dl>
        {Object.entries(funnel?.executionBlocked ?? {}).map(([code, count]) =>
          <dl key={code} className="mt-1">
            <Reading label={executionReasonLabel(code)} value={count} />
          </dl>)}
        {detail &&
          <p className="mt-2 whitespace-pre-wrap break-words text-[var(--muted)]">
            {detail}
          </p>}
      </details>
  );
}
