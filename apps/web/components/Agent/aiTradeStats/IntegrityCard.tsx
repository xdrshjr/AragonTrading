"use client";

import {
  coverageLabel, describeIntegrity, describeSettlement, economicsSourceLabel,
  evidenceVersionLabel, executionReasonLabel, integrityDetailText,
  readIntegrity, readSettlementValidation, settlementBasisLabel,
  type EconomicsSnapshot, type FeeAdjustment, type IntegritySnapshot,
  type SettlementValidationSnapshot,
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
    <div className="flex min-w-0 flex-wrap justify-between gap-x-2 gap-y-0.5
      text-[11px]">
      <dt className="min-w-0 break-words text-[var(--muted)]">{label}</dt>
      <dd className="tabular ml-auto min-w-0 max-w-full break-all text-right"
        style={{
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
    <section className="min-w-0 space-y-2 rounded-md border
      border-[var(--border)] bg-[var(--surface)] px-3 py-2
      [overflow-wrap:anywhere]"
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
  const detail = integrityDetailText(wire?.diagnosis?.details);
  return (
      <details className="min-w-0 space-y-2 text-[11px]">
        <summary className="cursor-pointer text-[var(--accent-ink)]
          focus-visible:rounded-sm focus-visible:outline
          focus-visible:outline-[var(--accent-ink)]">
          展开运行明细
        </summary>
        <p className="my-1 text-[10px] text-[var(--muted)]">
          {funnel?.scope ?? "当前运行段范围暂不可得"} ·
          {coverageLabel(funnel?.coverage)}
        </p>
        <FunnelReadings wire={wire} />
        {Object.entries(funnel?.executionBlocked ?? {}).map(([code, count]) =>
          <dl key={code} className="mt-1">
            <Reading label={executionReasonLabel(code)} value={count} />
          </dl>)}
        {detail &&
          <p className="mt-2 whitespace-pre-wrap break-words text-[var(--muted)]">
            {detail}
          </p>}
        <SettlementDetails value={wire?.settlementValidation} />
        <EvidenceDetails memory={wire?.memory} />
      </details>
  );
}

function FunnelReadings({ wire }: { wire: IntegritySnapshot | null }) {
  const funnel = wire?.funnel;
  const quota = wire?.entryQuota;
  return (
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
          <Reading label="完整费用样本"
            value={wire?.economics?.completeFeeTripCount} />
          <Reading label="未知样本" value={wire?.economics?.unknownTripCount} />
          <Reading label="全程手续费（含未平开仓）"
            value={wire?.economics?.sessionFeeUsd} money />
        </dl>
  );
}

function SettlementDetails({ value }: {
  value?: SettlementValidationSnapshot | null;
}) {
  const settlement = readSettlementValidation(value);
  return (
    <div className="min-w-0 space-y-1 border-t border-[var(--border)] pt-2">
      <h4 className="font-medium text-[var(--text)]">结算依据</h4>
      {settlement && <p className="text-[var(--muted)]">
        {settlement.status === "pending" ? "当前结算" : "上次结算"}
      </p>}
      <p className="whitespace-pre-wrap break-words">
        {describeSettlement(value)}
      </p>
      {settlement && <>
        <p className="text-[var(--muted)]">
          持仓依据：{settlementBasisLabel(settlement.identityBasis)}
          {" · "}{coverageLabel(settlement.amountCoverage ?? undefined)}
        </p>
        <p className="text-[var(--muted)]">
          价格依据：{settlementBasisLabel(settlement.priceBasis)}
          {" · "}{settlementBasisLabel(settlement.queryCoverage)}
        </p>
        <dl className="space-y-1 text-[var(--muted)]">
          <div><dt>结算时间</dt><dd className="tabular">
            {settlement.asOfTs != null ? fmtTime(settlement.asOfTs)
              : "暂不可得"}
          </dd></div>
          <div><dt>持仓标识</dt><dd className="break-all">
            {settlement.epochKey ?? "暂不可得"}
          </dd></div>
          <div><dt>结算标识</dt><dd className="break-all">
            {settlement.settlementKey ?? "暂不可得"}
          </dd></div>
        </dl>
        <FeeAdjustmentReadings adjustment={settlement.feeAdjustment} />
      </>}
    </div>
  );
}

function FeeAdjustmentReadings({ adjustment }: {
  adjustment?: FeeAdjustment | null;
}) {
  if (!adjustment) return null;
  if (adjustment.kind !== "aggregate_residual") {
    return <p className="text-[var(--muted)]">此类费用调整暂不可读</p>;
  }
  return (
    <div className="min-w-0 space-y-1 pt-1">
      <h5 className="font-medium text-[var(--text)]">往返费用调整</h5>
      <p className="text-[var(--muted)]">
        费用来源：{settlementBasisLabel(adjustment.feeBasis)}
      </p>
      <dl className="space-y-1">
        <Reading label="交易所往返手续费"
          value={adjustment.exchangeTripFeeUsd} money />
        <Reading label="本地已记手续费"
          value={adjustment.bookedLocalFeeUsd} money />
        <Reading label="本次费用差额"
          value={adjustment.residualFeeUsd} money />
      </dl>
    </div>
  );
}

function EvidenceDetails({ memory }: {
  memory?: IntegritySnapshot["memory"];
}) {
  const summaryVersion = memory?.summaryEvidenceVersion;
  return (
    <div className="min-w-0 space-y-1 border-t border-[var(--border)] pt-2">
      <h4 className="font-medium text-[var(--text)]">上下文证据版本</h4>
      <p className="text-[var(--muted)]">
        当前记录 {evidenceVersionLabel(memory?.evidenceVersion)}
        {" · "}{summaryVersion == null ? "摘要证据未确认"
          : `摘要 ${evidenceVersionLabel(summaryVersion)}`}
      </p>
      {memory?.legacyNarrative && <p className="text-[var(--muted)]">
        包含旧叙述，需以当前事实核验
      </p>}
    </div>
  );
}
